"""SOULHEALTH Demo API（阶段五：产品化 + 登录鉴权）。

启动：python run_api.py → 前端 http://127.0.0.1:8000/ ，Swagger /docs
首次启动会在控制台打印自动创建的管理员账号密码（仅打印一次），登录后请立即修改。

接口契约：
  -- 鉴权 --
  POST   /api/auth/login                    登录 {username, password} → {token, user}
  POST   /api/auth/register                 自助注册普通用户 {username, password, display_name?}
  GET    /api/auth/me                       当前登录用户信息
  POST   /api/auth/change_password          修改自己的密码 {old_password, new_password}
  -- 管理员 --
  GET    /api/admin/users                   用户列表（仅 admin）
  POST   /api/admin/users                   创建用户（仅 admin）{username, password, role, display_name?}
  PATCH  /api/admin/users/{uid}             启用/停用用户（仅 admin）{disabled}
  DELETE /api/admin/users/{uid}             删除用户（仅 admin，名下档案保留、owner 置空）
  -- 档案（均需登录；普通用户只能访问自己名下档案，admin 可访问全部）--
  GET    /api/health                        运行状态
  GET    /api/selftest/vision               视觉链路自检
  GET    /api/patients?query=               档案检索列表（姓名模糊 或 身份证后四位精确）
  POST   /api/patients                      建立/找回档案 {name?, id_last4?, sex?, age_years?, ...}
                                            → {patient, created}；身份匹配唯一依据是
                                              姓名+身份证后四位精确匹配，未提供后四位则始终新建
  GET    /api/patients/{pid}                档案快照
  PATCH  /api/patients/{pid}                更新基础信息
  DELETE /api/patients/{pid}                删除档案（级联）
  GET    /api/patients/{pid}/timeline       指标时间序列 ?code=ALT
  POST   /api/patients/{pid}/notes          添加症状/主诉备注 {text}
  POST   /api/patients/{pid}/observations   手动录入化验指标
  POST   /api/patients/{pid}/findings       手动录入影像/查体所见
  POST   /api/patients/{pid}/impressions    手动录入诊断提示/超声印象
  POST   /api/documents/upload              multipart 上传报告图片，视觉模型抽取入档
  GET    /api/documents/{doc_id}            单文档抽取结果
  POST   /api/patients/{pid}/ask            健康问答（真实模型，MOCK 下明确拒绝）
  POST   /api/analyze                       运行 Agent 分析 {patient_id}
  GET    /api/patients/{pid}/analyses       历次分析列表
  GET    /api/analyses/{aid}                单次分析详情（可回放）
  GET    /api/patients/{pid}/reports        报告列表
  GET    /api/reports/{rid}/download        报告下载
"""
from __future__ import annotations

import datetime
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config
from .agent import orchestrator, qa
from .archive import repository as repo
from .ingest.pipeline import ingest_document
from .ingest.vision_llm import ExtractionError, vision_selftest

app = FastAPI(title="SOULHEALTH AI 健康科研平台 Demo", version="0.7.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_ALLOWED_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
TITLES = {"health_analysis": "个性化健康分析报告", "tea_plan": "药食同源代茶饮建议"}


@app.on_event("startup")
def _startup() -> None:
    repo.init()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", **config.runtime_info()}


@app.get("/api/selftest/vision")
def selftest_vision(authorization: Optional[str] = Header(default=None)) -> dict:
    """视觉链路自检：发一张已知颜色的探测图，确认所配模型确实能收到图像。
    上传报告图片报「模型没收到图像」时先跑这个。需登录（任意角色）。"""
    _require_user(authorization)
    return vision_selftest()


# ================================================================== 鉴权

def _user_from_header(authorization: Optional[str]) -> dict:
    try:
        token = auth.extract_bearer_token(authorization)
        payload = auth.decode_token(token)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    user = repo.get_user(payload["uid"])
    if user is None or user.get("disabled"):
        raise HTTPException(401, "账号不存在或已被停用，请重新登录")
    return user


def _require_user(authorization: Optional[str]) -> dict:
    """FastAPI 里更地道的写法是 Depends()，这里为保持与项目其余部分一致的
    "显式函数调用、少框架魔法"风格，直接在每个端点里调用本函数取当前用户。"""
    return _user_from_header(authorization)


def _require_admin(authorization: Optional[str]) -> dict:
    user = _user_from_header(authorization)
    if user["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def _owns_or_admin(patient: dict, user: dict) -> None:
    if user["role"] == "admin":
        return
    if patient.get("owner_id") and patient["owner_id"] != user["id"]:
        raise HTTPException(403, "无权访问该档案（不属于当前登录用户）")


def _get_patient_scoped(pid: str, user: dict) -> dict:
    p = repo.get_patient(pid)
    if p is None:
        raise HTTPException(404, f"患者不存在: {pid}")
    _owns_or_admin(p, user)
    return p


@app.post("/api/auth/login")
def login(payload: dict = Body(...)) -> dict:
    try:
        user = repo.authenticate(payload.get("username") or "",
                                 payload.get("password") or "")
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    token = auth.create_token(user["id"], user["username"], user["role"])
    return {"token": token, "user": _public_user(user)}


@app.post("/api/auth/register")
def register(payload: dict = Body(...)) -> dict:
    """自助注册，固定角色为 user（普通用户）；管理员账号只能由已有管理员创建，
    防止任何人通过公开接口给自己开管理员权限。"""
    try:
        uid = repo.create_user(payload.get("username") or "",
                               payload.get("password") or "",
                               role="user",
                               display_name=payload.get("display_name"))
    except (ValueError, auth.AuthError) as exc:
        raise HTTPException(400, str(exc))
    user = repo.get_user(uid)
    token = auth.create_token(user["id"], user["username"], user["role"])
    return {"token": token, "user": _public_user(user)}


@app.get("/api/auth/me")
def whoami(authorization: Optional[str] = Header(default=None)) -> dict:
    return {"user": _public_user(_require_user(authorization))}


@app.post("/api/auth/change_password")
def change_password(payload: dict = Body(...),
                    authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    if not auth.verify_password(payload.get("old_password") or "", user["password_hash"]):
        raise HTTPException(400, "原密码不正确")
    try:
        new_hash = auth.hash_password(payload.get("new_password") or "")
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    from . import db as _db
    with _db.get_conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                    (new_hash, user["id"]))
    return {"ok": True}


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "display_name": user.get("display_name")}


# ================================================================== 管理员：用户管理

@app.get("/api/admin/users")
def admin_list_users(authorization: Optional[str] = Header(default=None)) -> dict:
    _require_admin(authorization)
    return {"users": repo.list_users()}


@app.post("/api/admin/users")
def admin_create_user(payload: dict = Body(...),
                      authorization: Optional[str] = Header(default=None)) -> dict:
    _require_admin(authorization)
    try:
        uid = repo.create_user(payload.get("username") or "",
                               payload.get("password") or "",
                               role=payload.get("role") or "user",
                               display_name=payload.get("display_name"))
    except (ValueError, auth.AuthError) as exc:
        raise HTTPException(400, str(exc))
    return {"user": _public_user(repo.get_user(uid))}


@app.patch("/api/admin/users/{uid}")
def admin_update_user(uid: str, payload: dict = Body(...),
                      authorization: Optional[str] = Header(default=None)) -> dict:
    admin_user = _require_admin(authorization)
    if uid == admin_user["id"] and payload.get("disabled"):
        raise HTTPException(400, "不能停用自己当前登录的账号")
    try:
        if "disabled" in payload:
            repo.set_user_disabled(uid, bool(payload["disabled"]))
    except KeyError:
        raise HTTPException(404, f"用户不存在: {uid}")
    return {"user": _public_user(repo.get_user(uid))}


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: str, authorization: Optional[str] = Header(default=None)) -> dict:
    admin_user = _require_admin(authorization)
    if uid == admin_user["id"]:
        raise HTTPException(400, "不能删除自己当前登录的账号")
    try:
        repo.delete_user(uid)
    except KeyError:
        raise HTTPException(404, f"用户不存在: {uid}")
    return {"deleted": uid}


# ================================================================== 档案身份

@app.get("/api/patients")
def patients_list(query: str | None = None,
                  authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    owner_filter = None if user["role"] == "admin" else user["id"]
    return {"patients": repo.list_patients(query, owner_id=owner_filter)}


@app.post("/api/patients")
def create_or_find_patient(payload: dict = Body(default={}),
                           authorization: Optional[str] = Header(default=None)) -> dict:
    """身份匹配唯一依据：姓名 + 身份证后四位精确匹配；未提供后四位则始终新建
    （不再使用"姓名+性别+年龄±2岁"模糊匹配）。档案归属当前登录用户。"""
    user = _require_user(authorization)
    pid, created = repo.find_or_create_patient(
        name=payload.get("name"), sex=payload.get("sex"),
        age_years=payload.get("age_years"),
        height_cm=payload.get("height_cm"), weight_kg=payload.get("weight_kg"),
        id_last4=payload.get("id_last4"), owner_id=user["id"])
    return {"patient_id": pid, "created": created, "patient": repo.get_patient(pid)}


@app.patch("/api/patients/{pid}")
def update_patient(pid: str, payload: dict = Body(...),
                   authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    payload.pop("owner_id", None)  # 归属只能通过管理员专用操作变更，不接受客户端直传
    try:
        repo.update_patient(pid, **payload)
    except KeyError:
        raise HTTPException(404, f"患者不存在: {pid}")
    return {"patient": repo.get_patient(pid)}


@app.get("/api/patients/{pid}")
def patient_snapshot(pid: str, authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    return repo.snapshot(pid)


@app.delete("/api/patients/{pid}")
def delete_patient(pid: str, authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    repo.delete_patient(pid)
    return {"deleted": pid}


@app.get("/api/patients/{pid}/timeline")
def timeline(pid: str, code: str | None = None,
            authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    return {"patient_id": pid, "code": code, "series": repo.get_timeline(pid, code)}


@app.post("/api/patients/{pid}/notes")
def add_note(pid: str, payload: dict = Body(...),
            authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "备注内容不能为空")
    nid = repo.add_note(pid, text)
    return {"note_id": nid, "notes": repo.list_notes(pid)}


# ---------------------------------------------------------------- 手动数据录入
# 与图片上传并存的第二种入口：填什么就是什么，无识别误差风险。

def _auto_flag(value_num, ref_low, ref_high) -> str | None:
    if value_num is None:
        return None
    if ref_high is not None and value_num > ref_high:
        return "H"
    if ref_low is not None and value_num < ref_low:
        return "L"
    if ref_low is not None or ref_high is not None:
        return "N"
    return None


@app.post("/api/patients/{pid}/observations")
def add_observation(pid: str, payload: dict = Body(...),
                    authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    code = (payload.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(400, "指标代码（code）不能为空，如 ALT / GLU / TG")
    value_num = payload.get("value_num")
    ref_low, ref_high = payload.get("ref_low"), payload.get("ref_high")
    flag = payload.get("abnormal_flag") or _auto_flag(value_num, ref_low, ref_high)
    observed_at = payload.get("observed_at") or datetime.date.today().isoformat()
    oid = repo.add_observation(
        pid, code=code, display=payload.get("display"), value_num=value_num,
        value_text=payload.get("value_text"), unit=payload.get("unit"),
        ref_low=ref_low, ref_high=ref_high, abnormal_flag=flag,
        observed_at=observed_at)
    return {"observation_id": oid, "abnormal_flag": flag}


@app.post("/api/patients/{pid}/findings")
def add_finding(pid: str, payload: dict = Body(...),
                authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    organ = (payload.get("organ") or "").strip()
    description = (payload.get("description") or "").strip()
    if not organ or not description:
        raise HTTPException(400, "脏器（organ）与所见描述（description）均不能为空")
    flags = payload.get("flags") or []
    if isinstance(flags, str):
        flags = [f.strip() for f in flags.split("、") if f.strip()]
    fid = repo.add_manual_finding(pid, organ, description, flags,
                                  observed_at=payload.get("observed_at"))
    return {"finding_id": fid}


@app.post("/api/patients/{pid}/impressions")
def add_impression(pid: str, payload: dict = Body(...),
                   authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "诊断提示内容不能为空")
    iid = repo.add_manual_impression(pid, text, observed_at=payload.get("observed_at"))
    return {"impression_id": iid}


# ---------------------------------------------------------------- 文档上传（视觉抽取）

@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    doc_type_hint: str | None = Form(default=None),
    engine: str | None = Form(default=None),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(patient_id, user)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIX:
        raise HTTPException(400, f"暂不支持的文件类型 {suffix}，"
                                 f"支持：{sorted(_ALLOWED_SUFFIX)}")
    stored = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(await file.read())
    try:
        result = ingest_document(patient_id, stored, doc_type_hint, engine,
                                 source_filename=file.filename)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc))       # 未配置密钥/无图等：明确指引，不给假答案
    except Exception as exc:
        raise HTTPException(500, f"抽取失败：{exc}")
    return result


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str, authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    doc = repo.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    _get_patient_scoped(doc["patient_id"], user)
    return doc


# ---------------------------------------------------------------- 健康问答

@app.post("/api/patients/{pid}/ask")
def ask_question(pid: str, payload: dict = Body(...),
                 authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    try:
        return qa.ask(pid, payload.get("question") or "")
    except qa.QAUnavailable as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"问答失败：{exc}")


# ---------------------------------------------------------------- 分析与报告

@app.post("/api/analyze")
def analyze(payload: dict = Body(...),
           authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    pid = payload.get("patient_id")
    if not pid:
        raise HTTPException(400, "缺少 patient_id")
    _get_patient_scoped(pid, user)
    try:
        return orchestrator.run_analysis(pid)
    except Exception as exc:
        raise HTTPException(500, f"分析失败：{exc}")


@app.get("/api/patients/{pid}/analyses")
def analyses_list(pid: str, authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    return {"patient_id": pid, "analyses": repo.list_analyses(pid)}


@app.get("/api/analyses/{aid}")
def analysis_detail(aid: str, authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    a = repo.get_analysis(aid)
    if a is None:
        raise HTTPException(404, f"分析不存在: {aid}")
    _get_patient_scoped(a["patient_id"], user)
    reports = [
        {"report_id": r["id"], "report_type": r["report_type"],
         "title": TITLES.get(r["report_type"], r["report_type"]),
         "format": r["format"], "path": r["path"],
         "download_url": f"/api/reports/{r['id']}/download"}
        for r in repo.list_reports(a["patient_id"], analysis_id=aid)
    ]
    return {"analysis_id": a["id"], "patient_id": a["patient_id"],
            "created_at": a["created_at"], "status": a["status"],
            "risk_tags": a["risk_tags"] or [],
            "mechanism_chain": a["mechanism_chain"] or {},
            "biocompute_plan": a["biocompute"] or [],
            "formula": a["formula"], "syndrome_tags": a["syndrome_tags"] or [],
            "interpretation": a["interpretation"],
            "trace": a["trace"] or [],
            "reports": reports}


@app.get("/api/patients/{pid}/reports")
def reports_list(pid: str, authorization: Optional[str] = Header(default=None)) -> dict:
    user = _require_user(authorization)
    _get_patient_scoped(pid, user)
    rows = repo.list_reports(pid)
    return {"patient_id": pid, "reports": [
        {**r, "title": TITLES.get(r["report_type"], r["report_type"]),
         "download_url": f"/api/reports/{r['id']}/download"} for r in rows]}


@app.get("/api/reports/{rid}/download")
def download_report(rid: str, token: Optional[str] = None,
                    authorization: Optional[str] = Header(default=None)):
    auth_str = authorization or (f"Bearer {token}" if token else None)
    user = _require_user(auth_str)
    r = repo.get_report(rid)
    if r is None or not Path(r["path"]).exists():
        raise HTTPException(404, f"报告不存在: {rid}")
    _get_patient_scoped(r["patient_id"], user)
    fname = Path(r["path"]).name
    media = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"
             if r["format"] == "docx" else "text/markdown")
    return FileResponse(r["path"], media_type=media, filename=fname)


# ---------------------------------------------------------------- 前端静态托管
_static = Path(__file__).resolve().parent.parent / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
