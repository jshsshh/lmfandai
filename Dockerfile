# 步骤1: 使用微软官方的Playwright镜像作为基础
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# 步骤2: 在容器内部创建一个工作目录
WORKDIR /app

# 步骤3: 将我们的自动化脚本文件复制到容器中
COPY browser_automator.py .

# 步骤4: 【关键修复】安装所有需要的Python库
# 我们需要 playwright, websockets, 和 aiohttp
RUN pip install playwright websockets aiohttp

# 步骤5: (可选) 运行一次playwright install来确保浏览器驱动是最新的
# 虽然基础镜像已包含浏览器，但运行这个命令可以确保Python库和浏览器驱动完美匹配
RUN playwright install

# 步骤6: 设置容器启动时要执行的默认命令
CMD ["python", "browser_automator.py"]
