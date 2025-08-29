# 步骤1: 使用微软官方的、包含浏览器的Playwright基础镜像
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# 步骤2: 设置容器内的工作目录
WORKDIR /app

# 步骤3: 将当前目录下的所有文件（主要是browser_automator.py）复制到容器的/app/目录
COPY . .

# 步骤4: 安装所有必需的Python库
# playwright: 用于浏览器自动化
# websockets: 用于与您的VPS后端进行WebSocket通信
# aiohttp: 用于创建一个轻量级的HTTP服务器以响应Koyeb的健康检查
RUN pip install playwright websockets aiohttp

# 步骤5: 运行playwright install命令
# 这是一个最佳实践，它会下载并链接Python库所需的浏览器驱动，确保完美兼容
RUN playwright install

# 步骤6: 设置容器启动时要执行的默认命令
CMD ["python", "browser_automator.py"]
