import runpy


def _sync_check_main():
    return runpy.run_path("tools/sync_check.py")["main"]


def test_sync_check_reports_na_when_main_topic_has_no_messages(capsys):
    _sync_check_main()("/main", [], {"/sensor": [1.0]})

    assert "/sensor: n/a (main topic has no messages)" in capsys.readouterr().out


def test_sync_check_reports_na_when_sensor_topic_has_no_messages(capsys):
    _sync_check_main()("/main", [1.0], {"/sensor": []})

    assert "/sensor: n/a (sensor topic has no messages)" in capsys.readouterr().out
