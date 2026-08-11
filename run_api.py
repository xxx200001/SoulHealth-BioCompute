"""启动 SOULHEALTH Demo API：python run_api.py → http://127.0.0.1:8000/docs"""
import os
import uvicorn

if __name__ == "__main__":
    host = os.getenv("SOULHEALTH_HOST", "127.0.0.1")
    port = int(os.getenv("SOULHEALTH_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
