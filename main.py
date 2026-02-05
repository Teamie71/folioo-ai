"""애플리케이션 진입점"""

import uvicorn


def main():
    """개발 서버 실행"""
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
