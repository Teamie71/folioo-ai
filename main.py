"""애플리케이션 진입점"""

import os

import uvicorn


def main():
    """개발 서버 실행"""
    host = os.getenv("UVICORN_HOST", "127.0.0.1")
    uvicorn.run("app.main:app", host=host, port=8000, reload=True)


if __name__ == "__main__":
    main()
