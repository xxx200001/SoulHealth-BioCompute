"""阶段五登录鉴权测试：密码哈希、令牌签发校验、管理员自动播种、
普通用户/管理员的档案隔离与权限边界。零三方依赖、零网络。

运行：python tests/test_auth.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SOULHEALTH_MOCK"] = "1"
os.environ["SOULHEALTH_BIOCOMPUTE"] = "mock"
os.environ["SOULHEALTH_SECRET_KEY"] = "test-secret-key-not-for-prod"
os.environ["SOULHEALTH_TOKEN_TTL_HOURS"] = "12"

from app import auth, config                              # noqa: E402
if config.DB_PATH.exists():
    config.DB_PATH.unlink()

from app.archive import repository as repo                # noqa: E402

PASSED = 0


def check(name, cond, detail=""):
    global PASSED
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    PASSED += 1


def main():
    repo.init()  # 触发管理员自动播种

    # ---------- 一、密码哈希 ----------
    h = auth.hash_password("correct-horse-battery-staple")
    check("密码哈希格式正确", h.startswith("pbkdf2_sha256$"))
    check("正确密码校验通过", auth.verify_password("correct-horse-battery-staple", h))
    check("错误密码校验失败", not auth.verify_password("wrong-password", h))
    try:
        auth.hash_password("123")
        check("过短密码应被拒绝", False)
    except auth.AuthError:
        check("过短密码被拒绝（<6 位）", True)

    # ---------- 二、令牌签发与校验 ----------
    tok = auth.create_token("uid-1", "alice", "user")
    payload = auth.decode_token(tok)
    check("令牌解码出正确的 uid/username/role",
          payload["uid"] == "uid-1" and payload["username"] == "alice"
          and payload["role"] == "user")

    tampered = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    try:
        auth.decode_token(tampered)
        check("篡改签名的令牌应校验失败", False)
    except auth.AuthError:
        check("篡改签名的令牌被拒绝", True)

    try:
        auth.decode_token("not-a-valid-token")
        check("格式非法的令牌应校验失败", False)
    except auth.AuthError:
        check("格式非法的令牌被拒绝", True)

    # 构造一个已过期的令牌，验证过期校验生效
    import json as _json
    expired_payload = {"uid": "uid-1", "username": "alice", "role": "user",
                       "iat": int(time.time()) - 100000, "exp": int(time.time()) - 1}
    payload_b64 = auth._b64url_encode(
        _json.dumps(expired_payload, ensure_ascii=False).encode("utf-8"))
    expired_tok = f"{payload_b64}.{auth._sign(payload_b64)}"
    try:
        auth.decode_token(expired_tok)
        check("过期令牌应校验失败", False)
    except auth.AuthError as exc:
        check("过期令牌被拒绝", "过期" in str(exc))

    # ---------- 三、管理员自动播种 ----------
    admins = [u for u in repo.list_users() if u["role"] == "admin"]
    check("首次启动自动创建至少一个管理员账号", len(admins) >= 1)
    admin_row = admins[0]
    check("管理员账号未被禁用", not admin_row["disabled"])

    # ---------- 四、用户创建与认证 ----------
    uid_alice = repo.create_user("alice_test", "alicepassword", role="user",
                                 display_name="Alice")
    user_row = repo.authenticate("alice_test", "alicepassword")
    check("正确用户名密码认证成功", user_row["id"] == uid_alice)
    try:
        repo.authenticate("alice_test", "wrongpassword")
        check("错误密码应认证失败", False)
    except auth.AuthError:
        check("错误密码认证失败（且不泄露具体原因）", True)
    try:
        repo.authenticate("nobody_xyz", "whatever")
        check("不存在的用户名应认证失败", False)
    except auth.AuthError as exc:
        check("不存在的用户名认证失败，报错与密码错误一致（防用户名枚举）",
              "用户名或密码错误" in str(exc))

    try:
        repo.create_user("alice_test", "anotherpassword")
        check("重复用户名应被拒绝", False)
    except ValueError:
        check("重复用户名注册被拒绝", True)

    # ---------- 五、停用账号 ----------
    repo.set_user_disabled(uid_alice, True)
    try:
        repo.authenticate("alice_test", "alicepassword")
        check("停用账号不应能登录", False)
    except auth.AuthError as exc:
        check("停用账号登录被拒绝", "停用" in str(exc))
    repo.set_user_disabled(uid_alice, False)
    check("重新启用后可正常登录",
          repo.authenticate("alice_test", "alicepassword")["id"] == uid_alice)

    # ---------- 六、档案归属与多用户隔离 ----------
    uid_bob = repo.create_user("bob_test", "bobpassword", role="user")
    pid_a, _ = repo.find_or_create_patient(name="患者甲", sex="male", age_years=40,
                                           id_last4="1111", owner_id=uid_alice)
    pid_b, _ = repo.find_or_create_patient(name="患者乙", sex="female", age_years=30,
                                           id_last4="2222", owner_id=uid_bob)
    alice_patients = repo.list_patients(owner_id=uid_alice)
    bob_patients = repo.list_patients(owner_id=uid_bob)
    check("普通用户只能看到自己名下的档案",
          any(p["id"] == pid_a for p in alice_patients)
          and not any(p["id"] == pid_b for p in alice_patients))
    check("不同普通用户之间档案互不可见",
          any(p["id"] == pid_b for p in bob_patients)
          and not any(p["id"] == pid_a for p in bob_patients))
    all_patients = repo.list_patients(owner_id=None)  # 管理员视角：不过滤
    check("管理员（owner_id=None）能看到全部用户的档案",
          any(p["id"] == pid_a for p in all_patients)
          and any(p["id"] == pid_b for p in all_patients))

    # ---------- 七、同名不同用户不会互相"找回"到对方档案 ----------
    pid_a2, created_a2 = repo.find_or_create_patient(
        name="患者甲", sex="male", age_years=41, id_last4="1111", owner_id=uid_alice)
    check("同一用户名下姓名+后四位精确匹配找回", not created_a2 and pid_a2 == pid_a)
    pid_a3, created_a3 = repo.find_or_create_patient(
        name="患者甲", sex="male", age_years=41, id_last4="1111", owner_id=uid_bob)
    check("同姓名+同后四位但 owner_id 不同 → 不会跨用户找到别人的档案（新建）",
          created_a3 and pid_a3 != pid_a)

    # ---------- 八、删除用户不删档案，只清空归属 ----------
    repo.delete_user(uid_bob)
    check("用户被删除后不能再通过用户名查到", repo.get_user_by_username("bob_test") is None)
    orphan = repo.get_patient(pid_b)
    check("被删用户名下的档案本身仍然保留，owner_id 置空",
          orphan is not None and orphan["owner_id"] is None)

    print(f"\n全部通过 ({PASSED} 项)")


if __name__ == "__main__":
    main()
