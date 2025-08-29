# browser_automator.py
import asyncio
import os
import json
import websockets
from playwright.async_api import async_playwright
import aiohttp

# --- 配置 ---
# 从环境变量中读取配置，如果不存在则使用默认值
BACKEND_WS_URL = os.environ.get("BACKEND_WS_URL")
LMARENA_URL = os.environ.get("LMARENA_URL", "https://lmarena.ai/")
ID_UPDATER_URL = os.environ.get("ID_UPDATER_URL", "http://localhost:5103/update") # 这是id_updater.py监听的地址
PAGE_SOURCE_UPDATER_URL = os.environ.get("PAGE_SOURCE_UPDATER_URL", "http://localhost:5102/internal/update_available_models") # 这是api_server.py监听的地址

# 检查关键配置
if not BACKEND_WS_URL:
    raise ValueError("关键环境变量 BACKEND_WS_URL 未设置！请在Koyeb平台配置它。")

# --- 全局状态 ---
# 使用一个字典来管理状态，避免使用全局变量
state = {
    "is_capture_mode_active": False,
    "page": None,
    "backend_websocket": None
}

# --- 核心功能 ---

async def send_to_backend(message):
    """安全地向后端WebSocket发送消息"""
    if state["backend_websocket"] and state["backend_websocket"].open:
        try:
            await state["backend_websocket"].send(json.dumps(message))
        except Exception as e:
            print(f"[Automator] 发送消息到后端时出错: {e}")
    else:
        print("[Automator] 无法发送消息，后端WebSocket未连接。")

async def execute_fetch_in_browser(request_id, payload):
    """在浏览器上下文中执行完整的fetch逻辑"""
    if not state["page"]:
        print("[Automator] 页面未初始化，无法执行fetch。")
        return

    # 这是将在浏览器中执行的JavaScript代码，几乎是原始JS的直接移植
    js_code = """
    async (args) => {
        const { requestId, payload, exposedSendChunk, exposedSendDone, exposedSendError } = args;
        const { is_image_request, message_templates, target_model_id, session_id, message_id } = payload;

        if (!session_id || !message_id) {
            const errorMsg = "从后端收到的会话信息 (session_id 或 message_id) 为空。";
            console.error(`[Browser JS] ${errorMsg}`);
            await exposedSendError(requestId, errorMsg);
            await exposedSendDone(requestId); // 依然发送DONE以结束客户端等待
            return;
        }

        const apiUrl = `/nextjs-api/stream/retry-evaluation-session-message/${session_id}/messages/${message_id}`;
        const httpMethod = 'PUT';
        
        const newMessages = [];
        let lastMsgIdInChain = null;

        if (!message_templates || message_templates.length === 0) {
            await exposedSendError(requestId, "从后端收到的消息列表为空。");
            await exposedSendDone(requestId);
            return;
        }

        for (let i = 0; i < message_templates.length; i++) {
            const template = message_templates[i];
            const currentMsgId = crypto.randomUUID();
            const parentIds = lastMsgIdInChain ? [lastMsgIdInChain] : [];
            const status = is_image_request ? 'success' : ((i === message_templates.length - 1) ? 'pending' : 'success');

            newMessages.push({
                role: template.role,
                content: template.content,
                id: currentMsgId,
                evaluationId: null,
                evaluationSessionId: session_id,
                parentMessageIds: parentIds,
                experimental_attachments: template.attachments || [],
                failureReason: null,
                metadata: null,
                participantPosition: template.participantPosition || "a",
                createdAt: new Date().toISOString(),
                updatedAt: new Date().toISOString(),
                status: status,
            });
            lastMsgIdInChain = currentMsgId;
        }

        const body = { messages: newMessages, modelId: target_model_id };

        try {
            const response = await fetch(apiUrl, {
                method: httpMethod,
                headers: { 'Content-Type': 'text/plain;charset=UTF-8', 'Accept': '*/*' },
                body: JSON.stringify(body),
                credentials: 'include'
            });

            if (!response.ok || !response.body) {
                const errorBody = await response.text();
                throw new Error(`网络响应不正常。状态: ${response.status}. 内容: ${errorBody}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { value, done } = await reader.read();
                if (done) {
                    await exposedSendDone(requestId);
                    break;
                }
                const chunk = decoder.decode(value);
                await exposedSendChunk(requestId, chunk);
            }
        } catch (error) {
            console.error(`[Browser JS] Fetch时出错:`, error);
            await exposedSendError(requestId, error.message);
        }
    }
    """
    try:
        # 使用page.evaluate来执行JS代码，并传递参数
        await state["page"].evaluate(js_code, {
            "requestId": request_id,
            "payload": payload,
            "exposedSendChunk": "pySendChunk", # 这些是我们在下面用expose_function暴露的函数名
            "exposedSendDone": "pySendDone",
            "exposedSendError": "pySendError"
        })
    except Exception as e:
        print(f"[Automator] 执行page.evaluate时出错: {e}")
        await send_to_backend({"request_id": request_id, "data": {"error": str(e)}})
        await send_to_backend({"request_id": request_id, "data": "[DONE]"})


async def handle_backend_message(message_str):
    """处理从后端WebSocket收到的消息"""
    try:
        message = json.loads(message_str)
        
        if "command" in message:
            command = message["command"]
            print(f"[Automator] 收到指令: {command}")
            if command in ['refresh', 'reconnect']:
                if state["page"]: await state["page"].reload()
            elif command == 'activate_id_capture':
                state["is_capture_mode_active"] = True
                print("[Automator] ID捕获模式已激活。")
            elif command == 'send_page_source':
                if state["page"]:
                    html = await state["page"].content()
                    async with aiohttp.ClientSession() as session:
                        await session.post(PAGE_SOURCE_UPDATER_URL, data=html.encode('utf-8'), headers={'Content-Type': 'text/html; charset=utf-8'})
                    print("[Automator] 页面源码已发送。")
            return

        request_id = message.get("request_id")
        payload = message.get("payload")

        if not request_id or not payload:
            print(f"[Automator] 收到无效消息: {message}")
            return
        
        print(f"[Automator] 收到任务 {request_id[:8]}，正在派发给浏览器...")
        await execute_fetch_in_browser(request_id, payload)

    except json.JSONDecodeError:
        print(f"[Automator] 无法解析收到的消息: {message_str}")
    except Exception as e:
        print(f"[Automator] 处理后端消息时发生未知错误: {e}")


async def main():
    """主程序入口"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"]) # 在Docker中通常需要--no-sandbox
        context = await browser.new_context()
        state["page"] = await context.new_page()
        print("[Automator] 浏览器已启动...")

        # --- 设置Python与浏览器JS的通信桥梁 ---
        await state["page"].expose_function("pySendChunk", lambda req_id, chunk: asyncio.create_task(send_to_backend({"request_id": req_id, "data": chunk})))
        await state["page"].expose_function("pySendDone", lambda req_id: asyncio.create_task(send_to_backend({"request_id": req_id, "data": "[DONE]"})))
        await state["page"].expose_function("pySendError", lambda req_id, err_msg: asyncio.create_task(send_to_backend({"request_id": req_id, "data": {"error": err_msg}})))

        # --- 设置网络请求拦截，用于ID捕获 ---
        async def handle_route(route):
            request = route.request
            match = "/nextjs-api/stream/retry-evaluation-session-message/" in request.url
            
            if match and state["is_capture_mode_active"]:
                print("[Automator] 侦测到符合条件的请求，正在捕获ID...")
                url_parts = request.url.split('/')
                session_id = url_parts[-3]
                message_id = url_parts[-1]
                
                state["is_capture_mode_active"] = False # 捕获一次后自动关闭
                print(f"[Automator] ID捕获成功: Session={session_id}, Message={message_id}。正在发送到ID更新器...")

                try:
                    async with aiohttp.ClientSession() as session:
                        await session.post(ID_UPDATER_URL, json={"sessionId": session_id, "messageId": message_id})
                    print("[Automator] ID已成功发送。")
                except Exception as e:
                    print(f"[Automator] 发送ID到更新器时失败: {e}")

            await route.continue_() # 让所有请求正常继续

        await state["page"].route("**/nextjs-api/stream/retry-evaluation-session-message/**", handle_route)

        # --- 导航到目标页面 ---
        try:
            await state["page"].goto(LMARENA_URL, wait_until="domcontentloaded", timeout=60000)
            print(f"[Automator] 已成功导航到: {LMARENA_URL}")
        except Exception as e:
            print(f"[Automator] 导航到LMArena页面失败: {e}")
            print("[Automator] 脚本将退出，请检查网络或URL。")
            await browser.close()
            return

        # --- 主循环：连接到后端并处理消息 ---
        while True:
            try:
                async with websockets.connect(BACKEND_WS_URL) as websocket:
                    state["backend_websocket"] = websocket
                    print(f"[Automator] ✅ 已成功连接到后端服务: {BACKEND_WS_URL}")
                    async for message in websocket:
                        await handle_backend_message(message)
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError) as e:
                print(f"[Automator] 🔌 与后端的连接已断开或被拒绝: {e}")
            except Exception as e:
                print(f"[Automator] WebSocket主循环发生未知错误: {e}")
            
            print("[Automator] 将在10秒后尝试重新连接...")
            state["backend_websocket"] = None
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Automator] 收到退出信号，正在关闭...")

