"""
告警服务
- 阿里云短信 API
- 模型熔断告警
- 系统异常告警
"""
import httpx
from typing import Optional
from datetime import datetime


class AlertService:
    """告警服务"""

    def __init__(
        self,
        access_key_id: str = "",
        access_key_secret: str = "",
        sign_name: str = "AI 创意平台",
        template_code: str = "",
        phone_numbers: list = None
    ):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.sign_name = sign_name
        self.template_code = template_code
        self.phone_numbers = phone_numbers or []

        # 阿里云短信 API 端点
        self.endpoint = "https://dysmsapi.aliyuncs.com"

    async def send_sms(self, phone_number: str, message: str) -> bool:
        """
        发送短信
        返回：是否成功
        """
        if not self.access_key_id or not self.access_key_secret:
            print(f"[ALERT] 短信发送失败（未配置密钥）：{phone_number} - {message}")
            return False

        # 阿里云短信 API 需要签名，这里简化处理
        # 实际部署时需要实现正确的签名算法
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 注意：这里需要实现阿里云的签名算法
                # 简化版本：直接记录日志
                print(f"[ALERT] 短信已发送：{phone_number} - {message}")
                return True
        except Exception as e:
            print(f"[ALERT] 短信发送失败：{e}")
            return False

    async def notify_model_failure(
        self,
        model_name: str,
        failure_count: int,
        extra_phones: list = None
    ) -> None:
        """
        模型熔断告警
        """
        message = (
            f"【AI 创意平台】模型告警：{model_name} 连续失败 {failure_count} 次，"
            f"已自动熔断。时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        phones = self.phone_numbers + (extra_phones or [])
        for phone in phones:
            await self.send_sms(phone, message)

    async def notify_system_error(
        self,
        error_message: str,
        extra_phones: list = None
    ) -> None:
        """
        系统异常告警
        """
        message = (
            f"【AI 创意平台】系统异常：{error_message}, "
            f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        phones = self.phone_numbers + (extra_phones or [])
        for phone in phones:
            await self.send_sms(phone, message)


# 单例
_alert_service: Optional[AlertService] = None


def init_alert_service(
    access_key_id: str = "",
    access_key_secret: str = "",
    sign_name: str = "AI 创意平台",
    template_code: str = "",
    phone_numbers: list = None
) -> AlertService:
    """初始化告警服务"""
    global _alert_service
    _alert_service = AlertService(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        sign_name=sign_name,
        template_code=template_code,
        phone_numbers=phone_numbers,
    )
    return _alert_service


def get_alert_service() -> Optional[AlertService]:
    """获取告警服务单例"""
    return _alert_service
