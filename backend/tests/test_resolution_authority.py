"""A destination label cannot turn another government's portal into readiness."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.visa_snapshot import SNAPSHOT_DATE
from app.visa_snapshot.models import OfficialPortalRecord, SourceEvidence
from app.visa_snapshot.resolution import resolve


def test_ready_portals_and_sources_require_actual_destination_authority():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for key, url, status, linking in [
            ('wrong-government', 'https://www.evisa.gov.kh/', 'verified_official_domain', ''),
            ('quarantined', 'https://quarantined.gov.la/', 'conflicted', ''),
            ('wrong-contractor', 'https://wrong-contractor.example/', 'verified_via_official_link', 'https://www.evisa.gov.kh/'),
            ('valid-government', 'https://www.evisa.gov.la/', 'verified_official_domain', ''),
            ('valid-contractor', 'https://valid-contractor.example/', 'verified_via_official_link', 'https://www.evisa.gov.la/partners'),
        ]:
            db.add(OfficialPortalRecord(snapshot_date=SNAPSHOT_DATE, portal_uid='LAO:' + key,
                destination_country='LAO', portal_kind='evisa_portal', url=url,
                hostnames=[], verification_status=status, official_linking_source=linking))
        for url in ['https://www.evisa.gov.kh/policy', 'https://www.evisa.gov.la/policy']:
            db.add(SourceEvidence(snapshot_date=SNAPSHOT_DATE, search_query='fixture',
                original_url=url, final_url=url, final_hostname=url.split('/')[2],
                source_authority='destination_foreign_ministry', applicable_jurisdiction='LAO',
                relevant_excerpt='Synthetic fixture', retrieved_at='2026-09-09T00:00:00Z',
                content_hash='fixture', verification_status='verified'))
        db.commit()
        result = resolve(db, org_id='authority-fixture', create_tasks=False, answers={
            'passport_nationality': 'SGP', 'passport_issuing_country': 'SGP',
            'destination_country': 'LAO', 'lawful_country_of_residence': 'SGP',
            'travel_document_type': 'ordinary_passport', 'visa_category': 'evisa_tourist',
            'travel_purpose': 'tourism', 'arrival_date': '2026-10-01'})
        portals = result['checks']['official_portal']
        assert portals['verified_count'] == 2
        assert {p['url'] for p in portals['portals']} == {
            'https://www.evisa.gov.la/', 'https://valid-contractor.example/'}
        sources = result['checks']['source_evidence']
        assert sources['verified_count'] == 1 and sources['sample_urls'] == ['https://www.evisa.gov.la/policy']
    engine.dispose()
