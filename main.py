"""애플리케이션 진입점"""

import os

import uvicorn


def main():
    """개발 서버 실행"""
    host = os.getenv("UVICORN_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("UVICORN_RELOAD", "false").lower() == "true"
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
