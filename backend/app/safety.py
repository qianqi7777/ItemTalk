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


def risk_of(text, warning=False):
    value = re.sub(r"[\s\u200b-\u200d\ufeff]", "", normalize(text))
    # 空白、全角与大小写不能绕过同一条规则；这仍是有限规则，不宣称涵盖所有危险表达。
    if warning:
        levels = []
        for clause in re.split(r"[。；;，,]", value):
            if re.match(r"^(?:请)?(?:勿|不要|禁止|切勿|不可|不得|严禁|避免)", clause):
                continue
            levels.append(risk_of(clause))
        return "high" if "high" in levels else "medium" if "medium" in levels else "low"
    return "high" if HIGH.search(value) else "medium" if MEDIUM.search(value) else "low"
