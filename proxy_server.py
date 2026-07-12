#!/usr/bin/env python3
"""
HTTP 代理服务器 - 用于注入 thinking 参数到 AI 模型请求中
支持 Anthropic 和 OpenAI 协议
"""

import json
import logging
import os
import select
import signal
import socket as socket_module
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import yaml
from typing import Dict, Any, Optional
from urllib.parse import urlparse
from logging_filter import ThinkingContentFilter


class ProxyConfig:
    """配置管理类"""
    
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
    
    def get_proxy_host(self) -> str:
        # 优先使用环境变量，用于 Docker 等容器环境
        return os.getenv('PROXY_HOST', self.config['proxy']['host'])
    
    def get_proxy_port(self) -> int:
        # 优先使用环境变量
        port_env = os.getenv('PROXY_PORT')
        if port_env:
            return int(port_env)
        return self.config['proxy']['port']
    
    def get_thinking_config_from_model(self, model_config: Dict[str, Any]) -> Dict[str, Any]:
        """根据模型配置中的 thinking_intensity 生成 thinking 配置"""
        intensity = model_config.get('thinking_intensity', 'medium')
        
        # 思考强度映射表
        intensity_map = {
            'disabled': 0,
            'low': 8000,
            'medium': 16000,
            'high': 32000,
            'maximum': 64000
        }
        
        budget_tokens = intensity_map.get(intensity, 16000)
        
        # 如果模型配置中直接指定了 budget_tokens，则优先使用
        if 'budget_tokens' in model_config:
            budget_tokens = model_config['budget_tokens']
        
        # disabled 时返回 disabled 类型
        if intensity == 'disabled' or budget_tokens == 0:
            return {
                "type": "disabled",
                "budget_tokens": 0
            }
        
        return {
            "type": "enabled",
            "budget_tokens": budget_tokens
        }
    
    def get_model_config(self, model_name: str) -> Optional[Dict[str, str]]:
        """根据模型名称获取配置"""
        for model in self.config['models']:
            if model['name'] == model_name:
                return model
        return None
    
    def get_logging_config(self) -> Dict[str, str]:
        return self.config['logging']


class ProxyHandler(BaseHTTPRequestHandler):
    """代理请求处理器"""
    
    config: ProxyConfig = None
    
    def _setup_logging(self):
        """设置日志"""
        if not hasattr(self.__class__, '_logging_setup'):
            log_config = self.config.get_logging_config()
            level = getattr(logging, log_config['level'])
            
            # 创建日志格式化器
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            
            # 创建文件处理器
            file_handler = logging.FileHandler(log_config['file'], encoding='utf-8')
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            
            # 创建流处理器
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(level)
            stream_handler.setFormatter(formatter)
            
            # 创建并应用过滤器
            thinking_filter = ThinkingContentFilter()
            file_handler.addFilter(thinking_filter)
            stream_handler.addFilter(thinking_filter)
            
            # 配置根日志
            root_logger = logging.getLogger()
            root_logger.setLevel(level)
            root_logger.handlers.clear()
            root_logger.addHandler(file_handler)
            root_logger.addHandler(stream_handler)
            
            self.__class__._logging_setup = True
    
    def log_message(self, format: str, *args):
        """重写日志方法"""
        logging.info(f"{self.address_string()} - {format % args}")
    
    def _check_client_connected(self) -> bool:
        """主动检测客户端连接是否仍然有效
        
        使用 select 检查套接字的异常状态，以及 MSG_PEEK 检测正常关闭。
        返回 True 表示客户端仍在连接，False 表示已断开。
        """
        try:
            # 获取原始套接字
            sock = self.connection
            if not sock:
                return False
            
            # 使用 select 检查套接字的异常状态（超时 0 = 非阻塞）
            _, _, exceptional = select.select([], [], [sock], 0)
            if exceptional:
                # 套接字上有待处理的错误 = 对方已发送 RST
                return False
            
            # 尝试 MSG_PEEK 检测对方是否正常关闭（发送了 FIN）
            try:
                data = sock.recv(1, socket_module.MSG_PEEK)
                if data == b'':
                    # 对端已正常关闭连接（recv 返回空）
                    return False
            except (BlockingIOError, InterruptedError):
                # 没有数据可读但连接正常（非阻塞模式）
                pass
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                # 对端已重置连接
                return False
            
            return True
        except (OSError, AttributeError):
            return False
    
    def do_POST(self):
        """处理 POST 请求"""
        self._setup_logging()
        
        response = None  # 初始化，用于 finally 中的资源释放
        
        try:
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            request_data = json.loads(body.decode('utf-8'))
            
            logging.info(f"收到请求: {self.path}")
            logging.debug(f"原始请求体: {json.dumps(request_data, ensure_ascii=False)}")
            
            # 识别请求类型和模型
            provider, model_name = self._identify_request(request_data)
            if not provider:
                self._send_error_response(400, "无法识别请求类型或模型")
                return
            
            logging.info(f"识别到提供商: {provider}, 模型: {model_name}")
            
            # 获取模型配置
            model_config = self.config.get_model_config(model_name)
            if not model_config:
                self._send_error_response(404, f"未找到模型配置: {model_name}")
                return
            
            # 根据提供商进行请求格式转换
            if provider == 'anthropic':
                # OpenAI 格式 → Anthropic 格式转换（含 thinking 注入）
                modified_data = self._convert_openai_to_anthropic(request_data, model_config)
                logging.info("请求已转换为 Anthropic 格式")
            else:
                # OpenAI 原生请求仅注入 thinking
                modified_data = self._inject_thinking(request_data, provider, model_config)
                logging.info(f"注入 thinking 参数: {modified_data.get('thinking')}")
            
            logging.debug(f"转发请求体: {json.dumps(modified_data, ensure_ascii=False)}")
            
            # 转发请求
            response = self._forward_request(modified_data, model_config, provider)
            
            # 返回响应
            self._send_success_response(response)
            
        except json.JSONDecodeError as e:
            logging.error(f"JSON 解析错误: {e}")
            self._send_error_response(400, f"请求体解析失败: {str(e)}")
        except Exception as e:
            logging.error(f"处理请求时出错: {e}", exc_info=True)
            self._send_error_response(500, f"服务器内部错误: {str(e)}")
        finally:
            # 确保上游响应连接被关闭，释放资源
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    
    def _identify_request(self, data: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """识别请求类型（Anthropic 或 OpenAI）"""
        model = data.get('model', '')
        
        # 通过模型名称判断提供商
        if model.startswith('claude'):
            return 'anthropic', model
        elif model.startswith('gpt') or model.startswith('o1'):
            return 'openai', model
        
        # 通过请求结构判断
        if 'messages' in data and 'max_tokens' in data:
            # Anthropic 通常有 max_tokens
            return 'anthropic', model
        elif 'messages' in data:
            # OpenAI 格式
            return 'openai', model
        
        return None, None
    
    def _inject_thinking(self, data: Dict[str, Any], provider: str, model_config: Dict[str, Any]) -> Dict[str, Any]:
        """注入 thinking 参数"""
        modified_data = data.copy()
        thinking_config = self.config.get_thinking_config_from_model(model_config)
        
        if provider == 'anthropic':
            # Anthropic 格式：在顶层添加 thinking 字段
            modified_data['thinking'] = thinking_config
        elif provider == 'openai':
            # OpenAI 格式：在顶层添加 thinking 字段（如果支持）
            modified_data['thinking'] = thinking_config
        
        return modified_data
    
    def _convert_openai_to_anthropic(self, data: Dict[str, Any], model_config: Dict[str, Any]) -> Dict[str, Any]:
        """将 OpenAI 请求格式转换为 Anthropic 格式"""
        thinking_config = self.config.get_thinking_config_from_model(model_config)
        
        converted = {
            "model": data.get("model"),
            "max_tokens": data.get("max_tokens", 4096),
            "messages": [],
            "stream": data.get("stream", True),
            "thinking": thinking_config,  # 注入 thinking 参数
        }
        
        # 提取并转换 system 消息为顶层 system 参数
        system_messages = [m for m in data.get("messages", []) if m.get("role") == "system"]
        if system_messages:
            converted["system"] = "\n\n".join([m.get("content", "") for m in system_messages])
        
        # 复制其他可选参数
        if "temperature" in data:
            converted["temperature"] = data["temperature"]
        if "top_p" in data:
            converted["top_p"] = data["top_p"]
        if "stop" in data:
            # OpenAI stop 可以是字符串或数组，Anthropic 用 stop_sequences 数组
            stop_val = data["stop"]
            if isinstance(stop_val, str):
                converted["stop_sequences"] = [stop_val]
            elif isinstance(stop_val, list):
                converted["stop_sequences"] = stop_val
        
        # 转换 messages
        for msg in data.get("messages", []):
            role = msg.get("role")
            if role == "system":
                continue  # 已提取到顶层
            
            anthropic_msg = self._convert_message_openai_to_anthropic(msg)
            if anthropic_msg:
                converted["messages"].append(anthropic_msg)
        
        # 转换 tools 格式
        openai_tools = data.get("tools", [])
        if openai_tools:
            anthropic_tools = []
            for tool in openai_tools:
                func = tool.get("function", {})
                params = func.get("parameters", {"type": "object", "properties": {}})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": params,
                })
            converted["tools"] = anthropic_tools
        
        # 转换 tool_choice
        tool_choice = data.get("tool_choice")
        if tool_choice is not None and openai_tools:
            if tool_choice == "none":
                # 不发送 tools，模型不能调用工具
                converted.pop("tools", None)
            elif tool_choice == "auto":
                # 默认行为，Anthropic 有 tools 时会自动决定
                pass
            elif tool_choice == "required":
                # Anthropic 用 {"type": "any"} 表示必须调用工具
                converted["tool_choice"] = {"type": "any"}
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                # 指定调用某个特定工具
                func_name = tool_choice.get("function", {}).get("name", "")
                converted["tool_choice"] = {
                    "type": "tool",
                    "name": func_name,
                }
        
        return converted
    
    def _convert_message_openai_to_anthropic(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将单条 OpenAI 消息转换为 Anthropic 格式"""
        role = msg.get("role")
        content = msg.get("content") or ""
        
        if role == "user":
            return {"role": "user", "content": content}
        
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                # 有 tool_calls 的 assistant 消息使用 content 数组格式
                content_blocks = []
                if content:
                    content_blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": args,
                    })
                return {"role": "assistant", "content": content_blocks}
            else:
                return {"role": "assistant", "content": content or ""}
        
        elif role == "tool":
            # tool 消息转换为 user 角色的 tool_result 块
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content,
                    "is_error": msg.get("is_error", False),
                }]
            }
        
        return None
    
    def _forward_request(self, data: Dict[str, Any], model_config: Dict[str, str], provider: str) -> requests.Response:
        """转发请求到目标 API"""
        api_base = model_config['api_base']
        api_key = model_config['api_key']
        
        # 构建完整 URL
        if provider == 'anthropic':
            url = f"{api_base}/v1/messages"
            headers = {
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01'
            }
        elif provider == 'openai':
            url = f"{api_base}/chat/completions"
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}'
            }
        else:
            raise ValueError(f"不支持的提供商: {provider}")
        
        # 复制原始请求头（排除某些头）
        for key, value in self.headers.items():
            if key.lower() not in ['host', 'content-length', 'authorization', 'x-api-key']:
                headers[key] = value
        
        logging.info(f"转发请求到: {url}")
        
        # 发送请求
        response = requests.post(
            url,
            json=data,
            headers=headers,
            stream=True
        )
        
        return response
    
    def _send_success_response(self, response: requests.Response):
        """发送成功响应"""
        self.send_response(response.status_code)
        
        # 复制响应头
        for key, value in response.headers.items():
            if key.lower() not in ['transfer-encoding', 'connection']:
                self.send_header(key, value)
        
        self.end_headers()
        
        # 检查是否是流式响应
        content_type = response.headers.get('Content-Type', '')
        is_stream = 'text/event-stream' in content_type or 'stream' in content_type.lower()
        
        if is_stream:
            # 流式响应处理
            self._handle_stream_response(response)
        else:
            # 非流式响应处理
            self._handle_json_response(response, content_type)
    
    def _handle_stream_response(self, response: requests.Response):
        """处理流式响应，将 Anthropic 格式全面转换为 OpenAI 格式（含 thinking、text、tool_use）"""
        in_thinking_block = False
        in_text_block = False
        in_tool_use_block = False
        tool_use_id = None
        tool_use_name = None
        tool_use_index = 0
        thinking_buffer = []
        
        # 客户端断开监控：守护线程定期检测客户端连接状态
        # 一旦检测到断开，立即关闭上游连接，使 iter_content 抛出异常中断阻塞
        monitor_stop_event = threading.Event()
        
        def _monitor_client_connection():
            """守护线程：每 0.5 秒检测一次客户端连接状态"""
            while not monitor_stop_event.is_set():
                if not self._check_client_connected():
                    logging.info("监控线程检测到客户端已断开，强制关闭上游连接...")
                    try:
                        # 强制关闭上游流式连接，中断 iter_content 的阻塞读取
                        response.close()
                    except Exception as e:
                        logging.debug(f"关闭上游连接时出错（可忽略）: {e}")
                    return
                monitor_stop_event.wait(0.5)  # 每 0.5 秒检查一次
        
        monitor_thread = threading.Thread(
            target=_monitor_client_connection,
            daemon=True,
            name="client-monitor"
        )
        monitor_thread.start()
        
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    try:
                        chunk_text = chunk.decode('utf-8')
                        lines = chunk_text.split('\n')
                        output_lines = []  # 收集需要转发的行
                        
                        for line in lines:
                            should_forward = True  # 默认转发
                            
                            if line.startswith('data: '):
                                data_str = line[6:].strip()
                                if data_str and data_str != '[DONE]':
                                    try:
                                        data = json.loads(data_str)
                                        event_type = data.get('type', '')
                                        
                                        # 检测内容块开始
                                        if event_type == 'content_block_start':
                                            content = data.get('content_block', {})
                                            content_type = content.get('type', '')
                                            if content_type == 'thinking':
                                                in_thinking_block = True
                                                thinking_buffer = []
                                                logging.info("\n<think>")
                                                should_forward = False
                                            elif content_type == 'text':
                                                in_text_block = True
                                                logging.info("\n--- 文本输出开始 ---")
                                                should_forward = False
                                            elif content_type == 'tool_use':
                                                in_tool_use_block = True
                                                tool_use_id = content.get('id', '')
                                                tool_use_name = content.get('name', '')
                                                # 转换为 OpenAI tool_calls 格式（第一个 chunk：id + name）
                                                openai_chunk = {
                                                    "id": "chatcmpl-proxy",
                                                    "object": "chat.completion.chunk",
                                                    "created": 0,
                                                    "model": "claude",
                                                    "choices": [{
                                                        "index": 0,
                                                        "delta": {
                                                            "role": "assistant",
                                                            "content": None,
                                                            "tool_calls": [{
                                                                "index": tool_use_index,
                                                                "id": tool_use_id,
                                                                "type": "function",
                                                                "function": {
                                                                    "name": tool_use_name,
                                                                    "arguments": ""
                                                                }
                                                            }]
                                                        },
                                                        "finish_reason": None
                                                    }]
                                                }
                                                openai_line = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                                self.wfile.write(openai_line.encode('utf-8'))
                                                self.wfile.flush()
                                                logging.info(f"工具调用开始: {tool_use_name}({tool_use_id})")
                                                should_forward = False
                                        
                                        # 处理 delta 事件
                                        elif event_type == 'content_block_delta':
                                            delta = data.get('delta', {})
                                            delta_type = delta.get('type', '')
                                            
                                            # 处理 thinking 内容
                                            if in_thinking_block:
                                                delta_text = delta.get('thinking') or delta.get('text', '')
                                                if delta_text:
                                                    thinking_buffer.append(delta_text)
                                                    # 只在 DEBUG 级别时打印思考内容
                                                    if logging.getLogger().level <= logging.DEBUG:
                                                        print(delta_text, end='', flush=True)
                                                    # 转换为 OpenAI reasoning_content 格式
                                                    openai_chunk = {
                                                        "id": "chatcmpl-proxy",
                                                        "object": "chat.completion.chunk",
                                                        "created": 0,
                                                        "model": "claude",
                                                        "choices": [{
                                                            "index": 0,
                                                            "delta": {
                                                                "reasoning_content": delta_text
                                                            },
                                                            "finish_reason": None
                                                        }]
                                                    }
                                                    openai_line = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                                    self.wfile.write(openai_line.encode('utf-8'))
                                                    self.wfile.flush()
                                                should_forward = False
                                            
                                            # 处理文本内容 - 转换为 OpenAI delta.content 格式
                                            elif in_text_block:
                                                delta_text = delta.get('text', '')
                                                if delta_text:
                                                    # 只在 DEBUG 级别时打印文本内容
                                                    if logging.getLogger().level <= logging.DEBUG:
                                                        print(delta_text, end='', flush=True)
                                                    openai_chunk = {
                                                        "id": "chatcmpl-proxy",
                                                        "object": "chat.completion.chunk",
                                                        "created": 0,
                                                        "model": "claude",
                                                        "choices": [{
                                                            "index": 0,
                                                            "delta": {
                                                                "content": delta_text
                                                            },
                                                            "finish_reason": None
                                                        }]
                                                    }
                                                    openai_line = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                                    self.wfile.write(openai_line.encode('utf-8'))
                                                    self.wfile.flush()
                                                should_forward = False
                                            
                                            # 处理工具调用参数 - 转换为 OpenAI tool_calls arguments 格式
                                            elif in_tool_use_block and delta_type == 'input_json_delta':
                                                partial_json = delta.get('partial_json', '')
                                                if partial_json:
                                                    openai_chunk = {
                                                        "id": "chatcmpl-proxy",
                                                        "object": "chat.completion.chunk",
                                                        "created": 0,
                                                        "model": "claude",
                                                        "choices": [{
                                                            "index": 0,
                                                            "delta": {
                                                                "tool_calls": [{
                                                                    "index": tool_use_index,
                                                                    "function": {"arguments": partial_json}
                                                                }]
                                                            },
                                                            "finish_reason": None
                                                        }]
                                                    }
                                                    openai_line = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                                    self.wfile.write(openai_line.encode('utf-8'))
                                                    self.wfile.flush()
                                                should_forward = False
                                        
                                        # 处理内容块结束
                                        elif event_type == 'content_block_stop':
                                            if in_thinking_block:
                                                in_thinking_block = False
                                                logging.info("</think>\n")
                                                should_forward = False
                                            elif in_text_block:
                                                in_text_block = False
                                                logging.info("--- 文本输出结束 ---\n")
                                                should_forward = False
                                            elif in_tool_use_block:
                                                in_tool_use_block = False
                                                tool_use_index += 1
                                                logging.info(f"工具调用结束: {tool_use_name}")
                                                should_forward = False
                                        
                                        # message_delta / message_stop 转换为 OpenAI 格式
                                        elif event_type == 'message_delta':
                                            stop_reason = data.get('delta', {}).get('stop_reason', 'stop')
                                            # 映射 Anthropic stop_reason 到 OpenAI finish_reason
                                            finish_reason_map = {
                                                'end_turn': 'stop',
                                                'max_tokens': 'length',
                                                'stop_sequence': 'stop',
                                                'tool_use': 'tool_calls',
                                            }
                                            finish_reason = finish_reason_map.get(stop_reason, stop_reason)
                                            openai_chunk = {
                                                "id": "chatcmpl-proxy",
                                                "object": "chat.completion.chunk",
                                                "created": 0,
                                                "model": "claude",
                                                "choices": [{
                                                    "index": 0,
                                                    "delta": {},
                                                    "finish_reason": finish_reason
                                                }]
                                            }
                                            openai_line = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                                            self.wfile.write(openai_line.encode('utf-8'))
                                            self.wfile.flush()
                                            should_forward = False
                                        
                                        elif event_type == 'message_stop':
                                            # 发送 [DONE] 标记
                                            done_line = "data: [DONE]\n\n"
                                            self.wfile.write(done_line.encode('utf-8'))
                                            self.wfile.flush()
                                            should_forward = False
                                        
                                        # 其他事件（如 message_start, ping）保持原样转发
                                        else:
                                            logging.debug(f"[转发原始事件] {event_type}")
                                    
                                    except json.JSONDecodeError:
                                        pass
                            
                            # 收集需要转发的行
                            if should_forward:
                                output_lines.append(line)
                        
                        # 批量转发不需要转换的行（如 event: 行、空行等）
                        if output_lines:
                            output_text = '\n'.join(output_lines)
                            self.wfile.write(output_text.encode('utf-8'))
                            self.wfile.flush()
                            
                    except UnicodeDecodeError:
                        self.wfile.write(chunk)
                        self.wfile.flush()
        
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as e:
            # 客户端已断开连接（IDE 点击中断），关闭上游流式连接
            logging.info(f"客户端已断开连接（IDE 中断），正在中止上游请求: {e}")
            response.close()
        
        except AttributeError as e:
            # 监控线程已关闭上游连接，导致底层文件指针置空
            logging.info(f"上游连接已被监控线程关闭（客户端断开），中止处理: {e}")
        
        except OSError as e:
            # 套接字级别错误（可能是监控线程关闭了上游连接导致），同样需要关闭上游连接
            logging.info(f"套接字错误（客户端可能已断开），正在中止上游请求: {e}")
            try:
                response.close()
            except Exception:
                pass
        
        finally:
            # 告知监控线程停止
            monitor_stop_event.set()
            # 确保上游连接被关闭（如果尚未关闭）
            try:
                response.close()
            except Exception:
                pass
    
    def _handle_json_response(self, response: requests.Response, content_type: str):
        """处理 JSON 响应"""
        response_buffer = []
        
        # 客户端断开监控（与流式响应相同的保护机制）
        monitor_stop_event = threading.Event()
        
        def _monitor_client_connection():
            while not monitor_stop_event.is_set():
                if not self._check_client_connected():
                    logging.info("监控线程检测到客户端已断开，强制关闭上游连接...")
                    try:
                        response.close()
                    except Exception as e:
                        logging.debug(f"关闭上游连接时出错（可忽略）: {e}")
                    return
                monitor_stop_event.wait(0.5)
        
        monitor_thread = threading.Thread(
            target=_monitor_client_connection,
            daemon=True,
            name="client-monitor"
        )
        monitor_thread.start()
        
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    self.wfile.write(chunk)
                    if 'application/json' in content_type:
                        response_buffer.append(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as e:
            # 客户端已断开连接（IDE 点击中断），关闭上游流式连接
            logging.info(f"客户端已断开连接（IDE 中断），正在中止上游请求: {e}")
            response.close()
            return
        except AttributeError as e:
            # 监控线程已关闭上游连接，导致底层文件指针置空
            logging.info(f"上游连接已被监控线程关闭（客户端断开），中止处理: {e}")
            return
        except OSError as e:
            # 套接字级别错误
            logging.info(f"套接字错误（客户端可能已断开），正在中止上游请求: {e}")
            try:
                response.close()
            except Exception:
                pass
            return
        finally:
            monitor_stop_event.set()
            try:
                response.close()
            except Exception:
                pass
        
        # 尝试提取并打印思考内容
        if response_buffer and 'application/json' in content_type:
            try:
                response_text = b''.join(response_buffer).decode('utf-8')
                response_data = json.loads(response_text)
                self._extract_and_log_thinking(response_data)
            except Exception as e:
                logging.debug(f"无法提取思考内容: {e}")
    
    def _extract_and_log_thinking(self, response_data: Dict[str, Any]):
        """提取并打印思考内容"""
        # Anthropic 格式
        if 'content' in response_data:
            for content_block in response_data.get('content', []):
                if isinstance(content_block, dict) and content_block.get('type') == 'thinking':
                    thinking_text = content_block.get('thinking', content_block.get('text', ''))
                    if thinking_text:
                        logging.info("=" * 60)
                        logging.info("🤔 思考内容:")
                        logging.info("-" * 60)
                        logging.info(thinking_text)
                        logging.info("=" * 60)
        
        # OpenAI 格式（如果有的话）
        if 'choices' in response_data:
            for choice in response_data.get('choices', []):
                message = choice.get('message', {})
                if 'thinking' in message:
                    thinking_text = message['thinking']
                    if thinking_text:
                        logging.info("=" * 60)
                        logging.info("🤔 思考内容:")
                        logging.info("-" * 60)
                        logging.info(thinking_text)
                        logging.info("=" * 60)
    
    def _send_error_response(self, code: int, message: str):
        """发送错误响应"""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        
        error_response = {
            "error": {
                "message": message,
                "type": "proxy_error",
                "code": code
            }
        }
        
        self.wfile.write(json.dumps(error_response, ensure_ascii=False).encode('utf-8'))


def run_proxy_server(config_path: str = "config.yaml"):
    """启动代理服务器"""
    # 加载配置
    config = ProxyConfig(config_path)
    ProxyHandler.config = config
    
    host = config.get_proxy_host()
    port = config.get_proxy_port()
    
    # 创建服务器
    server = HTTPServer((host, port), ProxyHandler)
    
    print(f"HTTP 代理服务器已启动")
    print(f"监听地址: http://{host}:{port}")
    print(f"配置文件: {config_path}")
    print("按 Ctrl+C 停止服务器")
    print("-" * 50)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务器...")
        logging.info("收到关闭信号，正在关闭服务器...")
    finally:
        server.server_close()
        print("服务器已关闭")
        logging.info("服务器已关闭")


if __name__ == '__main__':
    import sys
    
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    run_proxy_server(config_file)
