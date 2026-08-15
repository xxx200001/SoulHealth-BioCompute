"""全局配置：路径、环境变量、运行模式判定。

阶段五模式语义（与此前相反：真实优先）：
- LLM_MODE:
    real          配置了 ANTHROPIC_API_KEY → 图片抽取 / 健康问答走真实 Claude；
    mock          显式 SOULHEALTH_MOCK=1 → 离线演示样例（且默认连带 biocompute=mock）；
    unconfigured  两者皆无 → 抽取/问答接口返回明确配置指引，绝不悄悄给假答案。
- BIOCOMPUTE_MODE 默认 real：AlphaFold DB / UniProt / Ensembl 均为免密钥公开接口，
  默认真实调用；EVO2 打分需 NVIDIA_API_KEY，未配置时如实标记 skipped（不出假分）。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
SAMPLE_DIR = DATA_DIR / "samples"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "soulhealth.db"

for _d in (DATA_DIR, UPLOAD_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    """极简 .env 加载（不覆盖已有环境变量）。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
LLM_MODEL: str = os.getenv("SOULHEALTH_LLM_MODEL", "claude-sonnet-4-6").strip()

# —— 关键改动：MOCK 只在显式声明时开启 ——
MOCK_MODE: bool = os.getenv("SOULHEALTH_MOCK", "").strip() == "1"
LLM_MODE: str = "mock" if MOCK_MODE else ("real" if ANTHROPIC_API_KEY else "unconfigured")

OCR_ENGINE: str = os.getenv("SOULHEALTH_OCR_ENGINE", "vision_llm").strip() or "vision_llm"

# —— 生物计算：默认真实（免密钥公共 API）；显式 MOCK 时默认连带离线 ——
_bio_env = os.getenv("SOULHEALTH_BIOCOMPUTE", "").strip()
BIOCOMPUTE_MODE: str = _bio_env or ("mock" if MOCK_MODE else "real")

NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "").strip()
AFDB_API: str = os.getenv("SOULHEALTH_AFDB_API",
                          "https://alphafold.ebi.ac.uk/api/prediction/").strip()
UNIPROT_API: str = os.getenv("SOULHEALTH_UNIPROT_API",
                             "https://rest.uniprot.org/uniprotkb/search").strip()
ENSEMBL_API: str = os.getenv("SOULHEALTH_ENSEMBL_API",
                             "https://rest.ensembl.org").strip()
# EVO2 推理服务地址：默认指向 WSL2 中自建的 evo2_server.py 服务。
# 对方电脑在 WSL2 中运行 evo2_server.py 后，Windows 侧通过 localhost:8899 访问。
# 如果使用 NVIDIA NIM 云端服务，改为对应 URL 并设置 NVIDIA_API_KEY。
EVO2_URL: str = os.getenv(
    "SOULHEALTH_EVO2_URL",
    "http://localhost:8899/v1/evo2/score").strip()
BIOCOMPUTE_FIXTURES = SAMPLE_DIR / "biocompute"

# 报告中是否打印真实姓名（默认关闭：报告仅用化名，姓名只存本地库）
REPORT_REAL_NAME: bool = os.getenv("SOULHEALTH_REPORT_REAL_NAME", "").strip() == "1"

# —— 登录鉴权 ——
# 用于对登录令牌签名的密钥。Demo 默认值仅用于本地演示；生产部署必须通过
# SOULHEALTH_SECRET_KEY 环境变量设置为随机长字符串，否则任何人都能伪造登录令牌。
_DEFAULT_DEV_SECRET = "soulhealth-demo-insecure-dev-secret-CHANGE-ME"
SECRET_KEY: str = os.getenv("SOULHEALTH_SECRET_KEY", "").strip() or _DEFAULT_DEV_SECRET
SECRET_KEY_IS_DEFAULT: bool = SECRET_KEY == _DEFAULT_DEV_SECRET
TOKEN_TTL_HOURS: float = float(os.getenv("SOULHEALTH_TOKEN_TTL_HOURS", "12"))

# 首次启动自动创建的管理员账号（仅当 users 表为空时创建一次）
DEFAULT_ADMIN_USERNAME: str = os.getenv("SOULHEALTH_ADMIN_USER", "admin").strip()
DEFAULT_ADMIN_PASSWORD: str = os.getenv("SOULHEALTH_ADMIN_PASSWORD", "").strip()


def runtime_info() -> dict:
    return {
        "llm_mode": LLM_MODE,
        "mock_mode": MOCK_MODE,          # 兼容旧字段
        "llm_model": LLM_MODEL,
        "ocr_engine": OCR_ENGINE,
        "biocompute_mode": BIOCOMPUTE_MODE,
        "evo2_url": EVO2_URL,
        "evo2_ready": "localhost" in EVO2_URL or "127.0.0.1" in EVO2_URL or bool(NVIDIA_API_KEY),
        "db_path": str(DB_PATH),
        "secret_key_is_default": SECRET_KEY_IS_DEFAULT,
    }
