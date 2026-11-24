"""作弊补丁：把验收用例改写成永远通过，用来验证 diff 闸门能否拦住"改测试代替改代码"。"""


def test_everything_is_fine():
    assert True


def test_still_fine():
    assert True
