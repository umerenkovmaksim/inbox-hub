import json
from pathlib import Path

from inbox_hub.cli.import_export import export_to_items

FX = Path(__file__).parents[1] / "fixtures" / "tg_export.json"


def test_export_mapping():
    data = json.loads(FX.read_text())
    items = list(export_to_items(data, -1002362006703, FX.parent))
    assert [i.external_key for i in items] == ["-1002362006703:10", "-1002362006703:11"]
    assert items[0].text == "Сдать ПЗ1 до пятницы https://disk.yandex.ru/d/AAA"
    assert items[0].published_at.isoformat() == "2026-09-02T07:15:00+00:00"
    assert items[1].author == "Храмов Д.Э."
