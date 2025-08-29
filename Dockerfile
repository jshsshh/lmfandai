FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
WORKDIR /app
COPY browser_automator.py .
# aiohttp用于发送ID更新请求
RUN pip install websockets aiohttp
CMD ["python", "browser_automator.py"]
