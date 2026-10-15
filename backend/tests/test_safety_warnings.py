"""真实长手册的停用设备提示不应被当成燃气操作；附加危险指令仍拒绝。"""

import pytest

from app.safety import risk_of


@pytest.mark.parametrize(
    "warning",
    [
        "在医院、火车优先座位旁、存在易燃气体之处、自动门或火警警报器旁，"
        "请关闭耳机和其他Bluetooth设备 [1]。",
        "在医院、火车优先座位旁、存在易燃气体之处、自动门或火警警报器旁需关闭耳机及其他蓝牙设备，以防微波影响电子设备或引发意外；",
        "在医院里、火车优先座位旁、存在易燃气体之处、自动门或火警警报器旁，"
        "请关闭耳机和其他Bluetooth设备，否则可能导致意外 [1]。",
        "在医院、火车优先座位旁、存在易燃气体之处、自动门或火警警报器旁须关闭耳机及Bluetooth设备。",
    ],
)
def test_device_shutdown_precaution_is_safe_only_in_warning_context(warning):
    assert risk_of(warning, warning=True) == "low"
    assert risk_of(warning) == "high"


@pytest.mark.parametrize(
    "warning",
    [
        "在存在易燃气体之处需关闭耳机及其他蓝牙设备，然后点火。",
        "在存在易燃气体之处需关闭耳机及其他蓝牙设备；然后短接保护。",
        "在存在易燃气体之处需关闭耳机及其他蓝牙设备，然后带电拆机。",
        "在存在易燃气体之处维修燃气阀门。",
        "不要带电拆机；然后短接保护。",
    ],
)
def test_precaution_does_not_hide_dangerous_operations(warning):
    assert risk_of(warning, warning=True) == "high"
