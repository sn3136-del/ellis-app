"""Workbook publication filtering agrees with QC's individual product state."""
import asyncio
import copy
import io

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from app import main


def _names(monkeypatch, rows, **filters):
    monkeypatch.setattr(main, '_tstation_rows', lambda *a, **kw: rows)
    monkeypatch.setattr(main, 'require_quality_control', lambda p: None)
    response = main.travel_database_export(db=None, p=None, **filters)

    async def consume():
        return b''.join([chunk async for chunk in response.body_iterator])

    workbook = load_workbook(io.BytesIO(asyncio.run(consume())), read_only=True)
    values = list(workbook['Data'].values)
    names = [r[values[0].index('visa_type_name')] for r in values[1:]]
    workbook.close()
    return names


@pytest.fixture
def products():
    return [
        dict(visa_type_name='Published sibling', _held=False, _route_held=True,
             _publication_state='partial', confidence_level='Low'),
        dict(visa_type_name='Held sibling', _held=True, _route_held=True,
             _publication_state='published', confidence_level='High'),
        dict(visa_type_name='Legacy published', _publication_state='published'),
        dict(visa_type_name='Legacy unpublished', _publication_state='partial'),
    ]


@pytest.mark.parametrize('publication, expected', [
    ('', ['Published sibling', 'Held sibling', 'Legacy published', 'Legacy unpublished']),
    ('published', ['Published sibling', 'Legacy published']),
    ('unpublished', ['Held sibling', 'Legacy unpublished']),
])
def test_export_filters_products_without_changing_state(monkeypatch, products, publication, expected):
    before = copy.deepcopy(products)
    assert _names(monkeypatch, products, publication=publication) == expected
    assert products == before


def test_publication_combines_with_existing_visa_type_filter(monkeypatch, products):
    assert _names(monkeypatch, products, publication='published', visa_type='sibling') == ['Published sibling']


@pytest.mark.parametrize('publication', ['held', 'all', 'Published', 'false', ' '])
def test_invalid_filter_rejected_before_reading_records(monkeypatch, publication):
    monkeypatch.setattr(main, 'require_quality_control', lambda p: None)
    monkeypatch.setattr(main, '_tstation_rows', lambda *a, **kw: pytest.fail('must validate before reading'))
    with pytest.raises(HTTPException) as error:
        main.travel_database_export(publication=publication, db=None, p=None)
    assert error.value.status_code == 422
