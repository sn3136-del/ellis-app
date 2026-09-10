"""Country ownership of the existing reviewed government-source allowlist.

This never admits a new official host. Exceptional ownership is taken from
the mission/ministry review documented alongside authority.GOV_SUFFIXES, not
from a site's registrable domain or an embassy's physical location. Regional
and shared hosts without a single competent country remain unassigned.
"""
import re

from .authority import GOV_SUFFIXES, EXACT_OFFICIAL_HOSTS, is_government_host
from .registry import iso3

_BY_COUNTRY = {
    'VAT': ('vaticanstate.va', 'vatican.va'),
    'VNM': ('chinhphu.vn', 'baochinhphu.vn', 'vietnamembassy.org.uk'),
    'IRL': ('ireland.ie', 'irishimmigration.ie'),
    'TJK': ('mfa.tj', 'evisa.tj'), 'BHR': ('bahrain.bh',),
    'CUB': ('cubaminrex.cu', 'evisacuba.cu'), 'MUS': ('govmu.org',),
    'AND': ('govern.ad',), 'DNK': ('nyidanmark.dk', 'um.dk'),
    'BEN': ('evisa.bj', 'beninembassy.us'), 'GNB': ('cgrgba.gw',),
    'LUX': ('guichet.public.lu', 'mae.gouvernement.lu'),
    'ROU': ('mae.ro', 'evisa.mae.ro'), 'BFA': ('visaburkina.bf', 'applicant.visaburkina.bf'),
    'SMR': ('esteri.sm',), 'RUS': ('kremlin.ru', 'government.ru', 'kdmid.ru', 'mid.ru'),
    'MNG': ('consul.mn', 'evisa.mn'), 'ESP': ('boe.es',),
    'MAR': ('consulat.ma',), 'SAU': ('ksavisa.sa', 'www.visitsaudi.com', 'visa.visitsaudi.com'),
    'FSM': ('fsmembassy.fm',), 'MLI': ('maliembassy.us',),
    'CMR': ('diplocam.cm', 'ambacamcaire.com', 'evisacam.cm'),
    'GNQ': ('equatorialguinea-evisa.com',), 'YEM': ('yemenevisa.org',),
    'ERI': ('embassyeritrea.org',), 'PAN': ('embassyofpanama.org', 'panamaconsulatehk.com'),
    'ARE': ('uae-embassy.org',),
    'NLD': ('ind.nl', 'government.nl', 'rijksoverheid.nl', 'overheid.nl', 'netherlandsworldwide.nl'),
    'MOZ': ('mozambiquehighcommission.org.uk',), 'GHA': ('ghanaembassydc.org',),
    'SVK': ('minv.sk', 'mzv.sk'), 'BTN': ('bhutan.travel',),
    'BOL': ('bolivianembassy.co.uk',), 'BWA': ('botswanaembassy.org',),
    'NAM': ('namibia-botschaft.de', 'namibiaembassyusa.org'),
    'SDN': ('sudanembassybj.com', 'sudanembassy.org'), 'GUY': ('embassyofguyana.be',),
    'LBY': ('embassyoflibyadc.org',), 'NER': ('embassyofniger-usa.org',),
    'URY': ('embassyofuruguay.us', 'gub.uy'),
    'COG': ('ambacongo-us.org', 'diplobrazza.net'), 'LBR': ('liberianembassyus.org',),
    'HTI': ('haiti.org',), 'PAK': ('pakistan.hk',),
    'DEU': ('germany.info', 'bund.de', 'auswaertiges-amt.de', 'diplo.de'),
    'CHE': ('admin.ch',), 'BEL': ('belgium.be',),
    'SWE': ('government.se', 'regeringen.se', 'migrationsverket.se'),
    'NOR': ('government.no', 'regjeringen.no', 'udi.no'),
    'ISL': ('government.is', 'island.is'), 'BGR': ('government.bg', 'mfa.bg'),
    'ITA': ('esteri.it',), 'ARM': ('mfa.am',), 'IRN': ('mfa.ir', 'daftar.org'),
    'KGZ': ('e-gov.kg',), 'CHL': ('serviciomigraciones.cl',),
    'CIV': ('snedai.com', 'ci-embassyepay.org'), 'MMR': ('myanmarconsulatehk.org',),
    'SYC': ('seychelles.govtas.com',), 'KNA': ('knatravelform.kn',),
    'SYR': ('evisa.sy', 'syrembassy.cn'), 'BGD': ('bdcgny.org',),
    'KEN': ('kenyaembassydc.org',), 'LAO': ('laoembassy.com',),
    'LTU': ('migracija.lt',), 'FRA': ('service-public.fr',), 'AUT': ('gv.at',),
    'FIN': ('um.fi',), 'EST': ('vm.ee',), 'GRC': ('mfa.gr',), 'LIE': ('llv.li',),
    'STP': ('smf.st',), 'IRQ': ('evisa.iq',), 'SLE': ('evisa.sl',),
    'TCD': ('evisa.td',), 'GAB': ('evisa.dgdi.ga',),
    'PLW': ('bcbp.pw',), 'MHL': ('rmiimmigration.org',), 'PHL': ('meco.org.tw', 'www.meco.org.tw'),
}
_SPECIAL = {host: country for country, hosts in _BY_COUNTRY.items() for host in hosts}
# These hosts are shared or regional. A page needs separately bounded proof
# of competent authority; the mere host cannot ground an arbitrary country.
UNASSIGNED = {'un.int': 'Shared UN permanent-mission host; mission-path review needed.',
              'europa.eu': 'Shared EU host; only the existing EUR-Lex competence rule applies.',
              'gov.krd': 'Regional authority; do not infer nationwide Iraqi admission rules.'}


def suffix_owner(suffix):
    if suffix in _SPECIAL:
        return _SPECIAL[suffix]
    if suffix in ('gov', 'mil'):
        return 'USA'
    # These are explicitly enumerated official government namespace suffixes,
    # not arbitrary ccTLDs or inferred ownership of commercial domains.
    match = re.fullmatch(r'(?:gov|gob|gouv|go|govt)\.([a-z]{2})', suffix)
    if match:
        return iso3('GB' if match[1] == 'uk' else match[1]) or None
    if suffix in ('gc.ca', 'canada.ca', 'gouv.qc.ca'):
        return 'CAN'
    return None


def government_owner(host):
    if not is_government_host(host):
        return None
    matches = sorted((s for s in (*GOV_SUFFIXES, *EXACT_OFFICIAL_HOSTS)
                      if host == s or host.endswith('.' + s)), key=len, reverse=True)
    for suffix in matches:
        owner = suffix_owner(suffix)
        if owner:
            return owner
    return None
