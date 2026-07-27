import minirepo


def test_module_imports():
    assert minirepo.clamp(3, 0, 5) == 3
