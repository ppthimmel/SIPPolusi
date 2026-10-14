from __future__ import annotations

import pytest

from osm.config import OsmSettings
from osm.models import BBox, Mode, Point

from .conftest import edge


def test_bbox_parse_and_area():
    bbox = BBox.parse("106.82,-6.18,106.83,-6.17")
    assert bbox.as_list() == [106.82, -6.18, 106.83, -6.17]
    assert bbox.area_km2() == pytest.approx(1.23, abs=0.02)


@pytest.mark.parametrize("text", ["", "1,2,3", "1,2,3,x", "nan,1,2,3", "2,1,1,2", "0,-91,1,0"])
def test_bbox_parse_rejects(text):
    with pytest.raises(ValueError):
        BBox.parse(text)


def test_point_range():
    with pytest.raises(ValueError):
        Point(106.8, 106.0)  # lat 106 (UT-BE-02b)


def test_mode_directions():
    two_way = edge(1, 10, 20)
    one_way = edge(2, 10, 20, oneway=True)
    no_bike = edge(3, 10, 20, bike=False)
    assert list(Mode.WALK.directions(one_way)) == [(10, 20), (20, 10)]
    assert list(Mode.BIKE.directions(one_way)) == [(10, 20)]
    assert list(Mode.BIKE.directions(two_way)) == [(10, 20), (20, 10)]
    assert list(Mode.BIKE.directions(no_bike)) == []


def test_settings_accepts_sqlalchemy_dsn():
    s = OsmSettings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@h:5432/railway")
    assert s.database_url == "postgresql://u:p@h:5432/railway"
    assert s.study_area.contains(Point(106.8272, -6.1754))  # Monas
    assert not s.study_area.contains(Point(106.55, -5.75))  # Kepulauan Seribu
