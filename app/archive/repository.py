"""健康档案库：patients / documents / observations / findings / notes 的读写，
以及供 AI Agent 使用的档案快照（snapshot）。

阶段五关键能力：
- find_or_create_patient()：按 姓名(归一化)+性别 精确、年龄 ±2 岁容差匹配已有
  档案，命中即复用并更新——同一个人多次使用不再裂变成多个 UUID；
- list_patients() / touch() / delete_patient()：前端"我的档案"入口的数据支撑；
- add_note() / list_notes()：症状描述、主诉等自由文本入档（需求文档输入类型之一）；
- 分析结果补存 formula 与 trace，历史分析可在前端完整回放。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import secrets as _secrets

from .. import auth, config, db
from ..schemas import ExtractionResult

import re as _re
_ID4_RE = _re.compile(r"^\d{3}[\dXx]$")  # 身份证号后四位：3位数字+1位数字或校验位X


def _norm_id4(id_last4):
    if not id_last4:
        return None
    v = str(id_last4).strip().upper()
    return v if _ID4_RE.match(v) else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return uuid.uuid4().hex


def _norm_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = "".join(str(name).split()).lower()
    return n or None


def init() -> None:
    db.init_db()
    _seed_default_admin()


def _seed_default_admin() -> Optional[str]:
    """首次启动（users 表为空）时自动创建一个管理员账号，避免"没有账号无法
    登录"的先有鸡还是先有蛋问题。密码优先用 SOULHEALTH_ADMIN_PASSWORD；
    未设置时随机生成一个 12 位密码并打印到控制台（仅此一次，请立即保存/修改）。
    返回明文密码（仅供 run_api.py 等启动脚本打印，不会再次可查）；
    非首次启动（已存在用户）时返回 None。"""
    if list_users():
        return None
    password = config.DEFAULT_ADMIN_PASSWORD or _secrets.token_urlsafe(9)
    create_user(config.DEFAULT_ADMIN_USERNAME, password, role="admin",
               display_name="系统管理员")
    print("=" * 60)
    print(f"[SOULHEALTH] 首次启动，已自动创建管理员账号：")
    print(f"  用户名：{config.DEFAULT_ADMIN_USERNAME}")
    print(f"  密码　：{password}")
    print("  请登录后立即修改密码；此密码仅在此打印一次，不会再次显示。")
    print("=" * 60)
    return password


# ---------------------------------------------------------------- users（登录鉴权）

def create_user(username: str, password: str, role: str = "user",
                display_name: Optional[str] = None) -> str:
    username = (username or "").strip()
    if not username:
        raise ValueError("用户名不能为空")
    if role not in ("user", "admin"):
        raise ValueError("role 必须是 user 或 admin")
    if get_user_by_username(username) is not None:
        raise ValueError(f"用户名「{username}」已被占用")
    uid = _uid()
    pw_hash = auth.hash_password(password)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, display_name,"
            " created_at, disabled) VALUES (?,?,?,?,?,?,0)",
            (uid, username, pw_hash, role, display_name or username, _now()),
        )
    return uid


def get_user(uid: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?",
                           ((username or "").strip(),)).fetchone()
    return dict(row) if row else None


def list_users() -> List[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, role, display_name, created_at, disabled,"
            " (SELECT COUNT(*) FROM patients p WHERE p.owner_id=users.id) AS patient_count"
            " FROM users ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]


def set_user_disabled(uid: str, disabled: bool) -> None:
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE users SET disabled=? WHERE id=?",
                           (1 if disabled else 0, uid))
        if cur.rowcount == 0:
            raise KeyError(f"用户不存在: {uid}")


def delete_user(uid: str) -> None:
    """删除用户账号本身；名下患者档案保留（owner_id 置空，管理员仍可见并可重新分配）。"""
    with db.get_conn() as conn:
        conn.execute("UPDATE patients SET owner_id=NULL WHERE owner_id=?", (uid,))
        cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
        if cur.rowcount == 0:
            raise KeyError(f"用户不存在: {uid}")


def authenticate(username: str, password: str) -> dict:
    """校验用户名密码，成功返回用户行，失败抛 auth.AuthError（不泄露具体是
    用户名不存在还是密码错误，避免用户名枚举）。"""
    user = get_user_by_username(username)
    if user is None or not auth.verify_password(password, user["password_hash"]):
        raise auth.AuthError("用户名或密码错误")
    if user.get("disabled"):
        raise auth.AuthError("该账号已被停用，请联系管理员")
    return user


# ---------------------------------------------------------------- patients

def create_patient(sex: Optional[str] = None, age_years: Optional[int] = None,
                   height_cm: Optional[float] = None, weight_kg: Optional[float] = None,
                   pseudonym: Optional[str] = None, name: Optional[str] = None,
                   id_last4: Optional[str] = None,
                   owner_id: Optional[str] = None) -> str:
    pid = _uid()
    pseudonym = pseudonym or f"患者-{pid[:4].upper()}"
    now = _now()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO patients (id, name, name_norm, id_last4, pseudonym, sex,"
            " age_years, height_cm, weight_kg, owner_id, created_at, updated_at,"
            " last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, name, _norm_name(name), _norm_id4(id_last4), pseudonym, sex,
             age_years, height_cm, weight_kg, owner_id, now, now, now),
        )
    _maybe_derive_bmi(pid)
    return pid


def find_or_create_patient(name: Optional[str] = None, sex: Optional[str] = None,
                           age_years: Optional[int] = None,
                           height_cm: Optional[float] = None,
                           weight_kg: Optional[float] = None,
                           id_last4: Optional[str] = None,
                           owner_id: Optional[str] = None) -> Tuple[str, bool]:
    """找回或新建档案。身份匹配唯一依据是 **姓名 + 身份证后四位**（精确匹配）——
    不再使用"姓名+性别+年龄±2岁"模糊匹配：那种策略在同名同性别年龄相近时会
    把两个不同的人错误合并为一档，身份证后四位精确得多，且不需要处理生日
    跨年、年龄漂移等边界情况。

    未提供身份证后四位时，无法做可靠的身份判定，因此**始终新建档案**（不猜测），
    并在返回值中如实体现 created=True；界面应引导用户尽量填写后四位。

    owner_id 非空时限定在该用户名下查找/新建（多用户隔离，见 app/auth.py）。
    返回 (patient_id, created)。"""
    name_norm = _norm_name(name)
    id4 = _norm_id4(id_last4)

    if name_norm and id4:
        sql = "SELECT id FROM patients WHERE name_norm=? AND id_last4=?"
        args = [name_norm, id4]
        if owner_id is not None:
            sql += " AND owner_id=?"
            args.append(owner_id)
        with db.get_conn() as conn:
            row = conn.execute(sql, args).fetchone()
        if row:
            pid = row["id"]
            updates = {}
            if sex and not (get_patient(pid) or {}).get("sex"):
                updates["sex"] = sex
            if age_years is not None:
                updates["age_years"] = age_years
            if height_cm:
                updates["height_cm"] = height_cm
            if weight_kg:
                updates["weight_kg"] = weight_kg
            if updates:
                update_patient(pid, **updates)
            touch(pid)
            return pid, False
        pid = create_patient(sex=sex, age_years=age_years, height_cm=height_cm,
                             weight_kg=weight_kg, name=name, id_last4=id4,
                             owner_id=owner_id)
        return pid, True

    # 没有身份证后四位：不做任何模糊猜测，直接新建
    pid = create_patient(sex=sex, age_years=age_years, height_cm=height_cm,
                         weight_kg=weight_kg, name=name, owner_id=owner_id)
    return pid, True


def update_patient(pid: str, **fields) -> None:
    allowed = {"sex", "age_years", "height_cm", "weight_kg", "pseudonym", "name",
              "id_last4", "owner_id"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    if "name" in updates:
        updates["name_norm"] = _norm_name(updates["name"])
    if "id_last4" in updates:
        updates["id_last4"] = _norm_id4(updates["id_last4"])
    sets = ", ".join(f"{k}=?" for k in updates)
    with db.get_conn() as conn:
        cur = conn.execute(
            f"UPDATE patients SET {sets}, updated_at=? WHERE id=?",
            (*updates.values(), _now(), pid),
        )
        if cur.rowcount == 0:
            raise KeyError(f"患者不存在: {pid}")
    if "height_cm" in updates or "weight_kg" in updates:
        _maybe_derive_bmi(pid)


def touch(pid: str) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE patients SET last_seen_at=? WHERE id=?", (_now(), pid))


def get_patient(pid: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def list_patients(query: Optional[str] = None, limit: int = 50,
                  owner_id: Optional[str] = None) -> List[dict]:
    """患者列表（含资料/分析计数），按最近使用倒序——前端"我的档案"数据源。
    query 同时匹配姓名（模糊）与身份证后四位（精确）。
    owner_id 非空时只返回该用户名下的档案（普通用户）；为 None 时不过滤（管理员看全部）。"""
    sql = ("SELECT p.id, p.name, p.id_last4, p.pseudonym, p.sex, p.age_years,"
           " p.height_cm, p.weight_kg, p.last_seen_at, p.owner_id,"
           " (SELECT COUNT(*) FROM documents d WHERE d.patient_id=p.id) AS doc_count,"
           " (SELECT COUNT(*) FROM observations o WHERE o.patient_id=p.id) AS obs_count,"
           " (SELECT COUNT(*) FROM analyses a WHERE a.patient_id=p.id) AS analysis_count"
           " FROM patients p")
    where: list = []
    args: list = []
    qn = _norm_name(query)
    id4 = _norm_id4(query)
    if qn and id4:
        where.append("(p.name_norm LIKE ? OR p.id_last4=?)")
        args += [f"%{qn}%", id4]
    elif qn:
        where.append("p.name_norm LIKE ?")
        args.append(f"%{qn}%")
    if owner_id is not None:
        where.append("p.owner_id=?")
        args.append(owner_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY p.last_seen_at DESC LIMIT ?"
    args.append(limit)
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def delete_patient(pid: str) -> None:
    """级联删除该患者全部数据（FK 依赖逆序）。报告文件保留在磁盘由运维清理。"""
    with db.get_conn() as conn:
        for sql in (
            "DELETE FROM reports WHERE patient_id=?",
            "DELETE FROM analyses WHERE patient_id=?",
            "DELETE FROM observations WHERE patient_id=?",
            "DELETE FROM findings WHERE patient_id=?",
            "DELETE FROM patient_notes WHERE patient_id=?",
            "DELETE FROM documents WHERE patient_id=?",
            "DELETE FROM patients WHERE id=?",
        ):
            conn.execute(sql, (pid,))


def _maybe_derive_bmi(pid: str) -> None:
    p = get_patient(pid)
    if not p or not p.get("height_cm") or not p.get("weight_kg"):
        return
    h_m = float(p["height_cm"]) / 100.0
    bmi = round(float(p["weight_kg"]) / (h_m * h_m), 2)
    flag = "H" if bmi >= 24 else ("L" if bmi < 18.5 else "N")
    add_observation(pid, code="BMI", display="体质指数", value_num=bmi,
                    unit="kg/m²", ref_low=18.5, ref_high=23.9,
                    abnormal_flag=flag, observed_at=_now(), document_id=None)


# ---------------------------------------------------------------- notes

def add_note(pid: str, text: str) -> str:
    if get_patient(pid) is None:
        raise KeyError(f"患者不存在: {pid}")
    nid = _uid()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO patient_notes (id, patient_id, text, created_at)"
            " VALUES (?,?,?,?)", (nid, pid, text.strip(), _now()))
    touch(pid)
    return nid


def list_notes(pid: str) -> List[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, created_at FROM patient_notes"
            " WHERE patient_id=? ORDER BY created_at ASC", (pid,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- observations

def add_observation(pid: str, code: str, observed_at: str,
                    display: Optional[str] = None, value_num: Optional[float] = None,
                    value_text: Optional[str] = None, unit: Optional[str] = None,
                    ref_low: Optional[float] = None, ref_high: Optional[float] = None,
                    abnormal_flag: Optional[str] = None,
                    document_id: Optional[str] = None) -> str:
    oid = _uid()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO observations (id, patient_id, document_id, code, display,"
            " value_num, value_text, unit, ref_low, ref_high, abnormal_flag,"
            " observed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, pid, document_id, code.upper(), display, value_num, value_text,
             unit, ref_low, ref_high, abnormal_flag, observed_at, _now()),
        )
    return oid


def get_timeline(pid: str, code: Optional[str] = None) -> List[dict]:
    sql = "SELECT * FROM observations WHERE patient_id=?"
    args: list = [pid]
    if code:
        sql += " AND code=?"
        args.append(code.upper())
    sql += " ORDER BY observed_at ASC, created_at ASC"
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- documents

def save_document(pid: str, source_filename: str, stored_path: str,
                  extraction: ExtractionResult) -> str:
    if get_patient(pid) is None:
        raise KeyError(f"患者不存在: {pid}")
    doc_id = _uid()
    observed_at = extraction.exam_date or _now()[:10]
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, patient_id, doc_type, source_filename,"
            " stored_path, engine, exam_date, extraction_json, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, pid, extraction.document_type, source_filename, stored_path,
             extraction.engine, extraction.exam_date,
             json.dumps(extraction.to_dict(), ensure_ascii=False), _now()),
        )
        for f in extraction.findings:
            conn.execute(
                "INSERT INTO findings (id, patient_id, document_id, organ,"
                " description, flags_json, observed_at, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (_uid(), pid, doc_id, f.organ, f.description,
                 json.dumps(f.flags, ensure_ascii=False), observed_at, _now()),
            )
    for o in extraction.observations:
        add_observation(pid, code=o.code, display=o.display, value_num=o.value_num,
                        value_text=o.value_text, unit=o.unit, ref_low=o.ref_low,
                        ref_high=o.ref_high, abnormal_flag=o.abnormal_flag,
                        observed_at=observed_at, document_id=doc_id)
    p = get_patient(pid) or {}
    backfill = {}
    if not p.get("sex") and extraction.patient.sex in ("female", "male"):
        backfill["sex"] = extraction.patient.sex
    if not p.get("age_years") and extraction.patient.age_years:
        backfill["age_years"] = extraction.patient.age_years
    if backfill:
        update_patient(pid, **backfill)
    touch(pid)
    return doc_id


def get_document(doc_id: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["extraction"] = json.loads(d.pop("extraction_json"))
    return d


def list_documents(pid: str) -> List[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, doc_type, source_filename, engine, exam_date, created_at"
            " FROM documents WHERE patient_id=? ORDER BY created_at ASC", (pid,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_manual_finding(pid: str, organ: str, description: str,
                       flags: Optional[List[str]] = None,
                       observed_at: Optional[str] = None) -> str:
    """手动录入影像/查体所见（不依赖上传文档，document_id 为空）。
    阶段五：图片抽取逻辑不再作为主流程，检查所见改为结构化手动录入，
    准确性由填写者负责，系统不做"看图猜测"。"""
    if get_patient(pid) is None:
        raise KeyError(f"患者不存在: {pid}")
    fid = _uid()
    observed_at = observed_at or _now()[:10]
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO findings (id, patient_id, document_id, organ, description,"
            " flags_json, observed_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (fid, pid, None, organ.strip(), description.strip(),
             json.dumps(flags or [], ensure_ascii=False), observed_at, _now()),
        )
    touch(pid)
    return fid


def list_findings(pid: str) -> List[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT organ, description, flags_json, observed_at, document_id"
            " FROM findings WHERE patient_id=? ORDER BY observed_at ASC", (pid,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["flags"] = json.loads(d.pop("flags_json"))
        out.append(d)
    return out


# ---------------------------------------------------------------- snapshot

def snapshot(pid: str) -> dict:
    """档案全量快照：基础信息 + 文档 + 影像所见 + 指标时间序列 + 备注。
    Agent 与问答的标准输入，也是 analyses.input_snapshot_json。"""
    p = get_patient(pid)
    if p is None:
        raise KeyError(f"患者不存在: {pid}")
    timeline = get_timeline(pid)
    latest: dict = {}
    for obs in timeline:
        latest[obs["code"]] = obs
    return {
        "patient": p,
        "documents": list_documents(pid),
        "findings": list_findings(pid),
        "impressions": list_impressions(pid),
        "notes": list_notes(pid),
        "observations_timeline": timeline,
        "observations_latest": latest,
        "generated_at": _now(),
    }


def add_manual_impression(pid: str, text: str,
                          observed_at: Optional[str] = None) -> str:
    """手动录入诊断提示/超声印象（如"脂肪肝""胆囊息肉"）——对应真实报告单上
    "超声提示/诊断意见"这一行，与逐脏器的"检查所见"（findings）是两回事。
    图片抽取逻辑移除后，这是风险规则识别脂肪肝等印象类结论的唯一入口，
    照抄纸质报告上写的原话即可。"""
    if get_patient(pid) is None:
        raise KeyError(f"患者不存在: {pid}")
    iid = _uid()
    observed_at = observed_at or _now()[:10]
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO patient_impressions (id, patient_id, text, observed_at,"
            " created_at) VALUES (?,?,?,?,?)",
            (iid, pid, text.strip(), observed_at, _now()))
    touch(pid)
    return iid


def list_manual_impressions(pid: str) -> List[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, text, observed_at, created_at FROM patient_impressions"
            " WHERE patient_id=? ORDER BY observed_at ASC", (pid,)).fetchall()
    return [dict(r) for r in rows]


def list_impressions(pid: str) -> List[dict]:
    """合并两个来源：历史文档抽取产出的印象 + 手动录入的印象。"""
    out: List[dict] = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, exam_date, extraction_json FROM documents"
            " WHERE patient_id=? ORDER BY created_at ASC", (pid,)
        ).fetchall()
    for r in rows:
        ext = json.loads(r["extraction_json"])
        for text in ext.get("impressions", []):
            out.append({"text": text, "exam_date": r["exam_date"],
                        "document_id": r["id"]})
    for imp in list_manual_impressions(pid):
        out.append({"text": imp["text"], "exam_date": imp["observed_at"],
                    "document_id": None})
    return out


# ---------------------------------------------------------------- analyses / reports

def save_analysis(pid: str, input_snapshot: dict, risk_tags: list,
                  mechanism_chain: dict, biocompute: list,
                  formula: Optional[dict] = None,
                  syndrome_tags: Optional[list] = None,
                  interpretation: Optional[dict] = None,
                  status: str = "done") -> str:
    aid = _uid()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO analyses (id, patient_id, input_snapshot_json,"
            " risk_tags_json, mechanism_chain_json, biocompute_json,"
            " formula_json, syndrome_tags_json, interpretation_json,"
            " status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, pid,
             json.dumps(input_snapshot, ensure_ascii=False),
             json.dumps(risk_tags, ensure_ascii=False),
             json.dumps(mechanism_chain, ensure_ascii=False),
             json.dumps(biocompute, ensure_ascii=False),
             json.dumps(formula, ensure_ascii=False) if formula is not None else None,
             json.dumps(syndrome_tags, ensure_ascii=False)
             if syndrome_tags is not None else None,
             json.dumps(interpretation, ensure_ascii=False)
             if interpretation is not None else None,
             status, _now()),
        )
    touch(pid)
    return aid


def update_analysis_trace(aid: str, trace: list) -> None:
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE analyses SET trace_json=? WHERE id=?",
                           (json.dumps(trace, ensure_ascii=False), aid))
        if cur.rowcount == 0:
            raise KeyError(f"分析不存在: {aid}")


def get_analysis(aid: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id=?", (aid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in ("input_snapshot_json", "risk_tags_json", "mechanism_chain_json",
                "biocompute_json", "formula_json", "syndrome_tags_json",
                "interpretation_json", "trace_json"):
        d[key.replace("_json", "")] = json.loads(d.pop(key) or "null")
    return d


def list_analyses(pid: str) -> List[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, created_at FROM analyses"
            " WHERE patient_id=? ORDER BY created_at DESC", (pid,)
        ).fetchall()
    return [dict(r) for r in rows]


def save_report(analysis_id: str, pid: str, report_type: str,
                fmt: str, path: str) -> str:
    rid = _uid()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO reports (id, analysis_id, patient_id, report_type,"
            " format, path, created_at) VALUES (?,?,?,?,?,?,?)",
            (rid, analysis_id, pid, report_type, fmt, path, _now()),
        )
    return rid


def get_report(rid: str) -> Optional[dict]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return dict(row) if row else None


def list_reports(pid: str, analysis_id: Optional[str] = None) -> List[dict]:
    sql = "SELECT * FROM reports WHERE patient_id=?"
    args: list = [pid]
    if analysis_id:
        sql += " AND analysis_id=?"
        args.append(analysis_id)
    sql += " ORDER BY created_at ASC"
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def update_analysis_biocompute(aid: str, biocompute: list) -> None:
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE analyses SET biocompute_json=? WHERE id=?",
            (json.dumps(biocompute, ensure_ascii=False), aid))
        if cur.rowcount == 0:
            raise KeyError(f"分析不存在: {aid}")
