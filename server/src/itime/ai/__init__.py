"""AI 服务端基础能力。"""

# AIJob 引用独立文件中的会话表；导入包时先把该表注册到共享 metadata。
from itime.ai import conversation_models as conversation_models  # noqa: F401
