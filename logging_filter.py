"""
日志过滤器模块 - 根据日志级别过滤特定消息
"""

import logging


class ThinkingContentFilter(logging.Filter):
    """
    思考内容过滤器 - 在 INFO 级别时过滤思考相关的日志
    包括思考块的开始、结束标记和文本输出标记
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤日志记录
        - 如果日志级别是 INFO，过滤掉思考相关和文本输出的日志
        - 其他级别的日志直接通过
        """
        # 只在 INFO 级别时过滤
        if record.levelno == logging.INFO:
            message = record.getMessage()
            
            # 过滤思考块相关的日志
            if message.startswith("\n<think>"):
                return False
            if message.startswith("</think>"):
                return False
            
            # 过滤文本输出标记相关的日志
            if message.startswith("\n--- 文本输出开始 ---"):
                return False
            if message.startswith("--- 文本输出结束 ---"):
                return False
            
            # 过滤分隔线（思考内容的装饰）
            if message.startswith("=" * 60):
                return False
            if message.startswith("-" * 60):
                return False
            
            # 过滤思考内容标题
            if "🤔 思考内容:" in message:
                return False
        
        # 其他情况允许通过
        return True
