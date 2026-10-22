"""Curated low-risk help; unverified model output never becomes device instructions."""

import re

from app.safety import risk_of
from app.security import normalize

LABEL = "通用建议 · 未经该型号官方资料核验"
SENSITIVE = re.compile(
    r"固件|刷机|刷写|恢复出厂|重置|复位|拆|接线|电压|电流|功率|充电|防水|"
    r"剂量|医疗|燃气|校准|短接|绕过|多少伏|几伏|firmware|flash|reset|"
    r"disassembl|voltage|amper|watt|dosage|calibrat|waterproof"
)


def requires_evidence(question):
    value = re.sub(r"[\s\u200b-\u200d\ufeff]", "", normalize(question))
    return risk_of(question) != "low" or bool(SENSITIVE.search(value))


def general_advice(question):
    """Only reversible checks and concepts; no times, combinations, ratings or links."""
    if requires_evidence(question):
        return (
            "目前缺少适用于该型号的可靠资料，不能确认这项操作或参数。"
            "需要先核对完整型号、版本及对应资料；请补充铭牌、说明书相关页或官方支持链接。"
        )
    value = normalize(question)
    if re.search(r"dpi|灵敏度|指针|鼠标速度|pointer|sensitivity", value):
        body = (
            "DPI描述鼠标对移动的采样灵敏度，系统指针速度也会影响光标移动效果，"
            "两者不能直接等同。可以先记录当前设置，在电脑的鼠标或指针设置中小幅调整，"
            "逐次比较操作感受，不合适时恢复原设置。该型号的DPI档位、切换按键和"
            "驱动支持情况仍需对应资料确认。"
        )
    elif re.search(r"蓝牙|bluetooth|配对|pairing", value):
        body = (
            "如果设备支持蓝牙，可以打开电脑的蓝牙设置，检查蓝牙是否开启、设备是否"
            "已连接到其他主机，以及列表中是否出现对应设备。先记录列表名称与报错，"
            "不要删除现有配对。进入配对模式的按键及指示灯含义因型号而异，"
            "请提供底部开关、按键标识或相关说明页，再确认下一步。"
        )
    elif re.search(r"wifi|wi-fi|网络|联网|无线网", value):
        body = (
            "可以先用另一台已有连接的设备检查同一网络能否正常访问，区分网络问题与"
            "单台设备问题；核对网络名称，记录设备显示的完整报错。不要发送网络密码。"
            "该型号支持的频段、加密方式和配网按键，需要对应资料确认。"
        )
    elif re.search(r"清洁|擦|clean", value):
        body = (
            "如果只是外壳表面灰尘，可先停止使用，在设备可安全停止工作的前提下，"
            "用干燥柔软的布轻轻擦拭可触及表面。不要拆卸、向孔隙喷液体或使用溶剂。"
            "滤芯、内部部件、特殊涂层及需要消毒的设备应先核对对应清洁资料。"
        )
    elif re.search(r"连接|识别|驱动|connect|detect|driver", value):
        body = (
            "可以先检查电脑是否显示设备名称或报错，并记录操作系统版本、当前连接方式"
            "及问题出现的时机。若使用USB接收器，可查看电脑是否识别到它；不要安装"
            "来源不明的驱动。驱动下载和专用设置需先确认品牌、完整型号及官方支持页面。"
        )
    else:
        body = (
            "可以先收集不改变设备状态的信息：完整型号、设备类别、使用场景，以及"
            "问题出现前后的变化。若有指示灯或屏幕报错，请提供颜色、闪烁情况或完整文字；"
            "这些现象本身不能证明具体故障。请描述你想完成的操作，我会先协助缩小范围。"
        )
    return LABEL + "\n\n" + body
