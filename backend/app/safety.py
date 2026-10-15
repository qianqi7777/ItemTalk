"""问答与操作引导共用的风险规则；模型不能覆盖服务端拒绝决定。"""

import re

from app.security import normalize

HIGH = re.compile(
    "带电.{0,8}拆|拆.*电源|拆.{0,8}机壳|高压|燃气|短接|绕过.*保护|"
    "绕过.{0,12}安全|裸露.{0,8}(?:电|导线)|live.{0,15}(?:wire|disassembl)|"
    "highvoltage|gas|bypass.{0,12}(?:safety|protect)"
)
MEDIUM = re.compile(
    "(?:连接|拔|插).{0,8}电源|电源.{0,8}(?:接线|插|连接)|插座|"
    "插头|高温|热表面|hotsurface|powerconnection"
)
DEVICE_SHUTDOWN_WARNING = re.compile(
    r"在(?:医院、|火车优先座位旁、)*存在易燃气体之处"
    r"(?:、(?:自动门(?:或火警警报器旁)?|火警警报器旁))*[，,]?(?:请|需)关闭"
    r"(?:本)?耳机(?:和|及)其他(?:bluetooth|蓝牙)设备(?:\[\d+\])?"
    r"(?:[，,]以防微波影响电子设备或引发意外)?(?=[。；;]|$)"
)


def risk_of(text, warning=False):
    value = re.sub(r"[\s\u200b-\u200d\ufeff]", "", normalize(text))
    # 空白、全角与大小写不能绕过同一条规则；这仍是有限规则，不宣称涵盖所有危险表达。
    if warning:
        # 官方避险说明中的场所名并非燃气操作。只识别完整的关闭设备句式，
        # 不吞掉后续指令；用户操作请求仍走下方未经豁免的风险匹配。
        value = DEVICE_SHUTDOWN_WARNING.sub("关闭设备", value)
        levels = []
        for clause in re.split(r"[。；;，,]", value):
            if re.match(r"^(?:请)?(?:勿|不要|禁止|切勿|不可|不得|严禁|避免)", clause):
                continue
            levels.append(risk_of(clause))
        return "high" if "high" in levels else "medium" if "medium" in levels else "low"
    return "high" if HIGH.search(value) else "medium" if MEDIUM.search(value) else "low"
