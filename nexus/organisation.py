"""What kind of organisation a Capital IQ row is, and who owns it.

Capital IQ's "Health Care Equipment and Services" group mixes product
companies with hospitals, physician practices, nursing homes and clinics.
They can be useful clinical collaborators but are not industry sponsors, so
the output LABELS them (they are not removed):

  organisation_type   Company / Contract manufacturer / Hospital / health system /
                      Clinic / care provider
                      - from the company name and the opening of its Capital
                      IQ description (where Capital IQ says what it IS).
                      A description that says it develops, manufactures,
                      designs, sells ... makes it a Company whatever the name.
  parent_company      "X" when the description says "operates as a subsidiary
                      of X" (Capital IQ status "Operating Subsidiary")
"""
from __future__ import annotations

import re

COMPANY = "Company"
CONTRACT = "Contract manufacturer"
HOSPITAL = "Hospital / health system"
CLINIC = "Clinic / care provider"

_HOSP_NAME = re.compile(
    r"\b(hospitals?|health ?systems?|medical cent(?:er|re)s?|health ?care system|"
    r"healthcare system|infirmary|sanatorium)\b", re.I)
_CLIN_NAME = re.compile(
    r"(\bp\.?\s?c\.?$|\bpllc\b|\bp\.?a\.?$|medical group|medical associates|\bphysicians?\b|"
    r"physical therapy|\bclinics?\b|urgent care|\brehab\b|rehabilitation (?:center|services)|"
    r"\bnursing\b|home health|home care|hospice|surgery center|surgical center|\bdental\b|"
    r"orthodont|chiropractic|\bpediatrics?\b|dermatology|family medicine|primary care|"
    r"health center|healthcenter|care center|senior living|assisted living|vision care)", re.I)
_MAKER = re.compile(
    r"\b(develops?|developing|manufactures?|manufacturing|designs?|produces?|engineers|"
    r"discovers|commercializes|markets|sells|distributes|supplies|"
    r"developer|manufacturer|distributor|supplier|maker|wholesaler|"
    r"offers (?:a |an )?(?:software|platform|products?|devices?|systems?))\b", re.I)
_HOSP_DESC = re.compile(
    r"\b(operates|owns and operates|owns|operates as|is|runs)\s+(?:a |an |as a |as an )?"
    r"(?:\w+[- ]){0,4}?(hospitals?|health ?systems?|healthcare system|health care system|"
    r"health ?care network|healthcare network|medical cent(?:er|re)s?)\b", re.I)
_CLIN_DESC = re.compile(
    r"(\bprovides?\s+(?:\w+[ ,-]+){0,5}?(health ?care|medical|nursing|home health|"
    r"physical therapy|occupational therapy|rehabilitation|dental|therapy|care|clinical|"
    r"physician|surgical|veterinary|mental health|behavioral health|counseling|hospice|"
    r"diagnostic|treatment|imaging|laboratory testing) services"
    r"|\b(operates|owns and operates|operates as|is)\s+(?:a |an |as a |as an )?(?:\w+[- ]){0,4}?"
    r"(clinics?|practices?|nursing homes?|nursing facilit\w+|surgery cent\w+|urgent care|"
    r"physical therapy|rehabilitation cent\w+|treatment cent\w+|senior living|"
    r"assisted living|medical offices?|health practitioner)\b"
    r"|\bprovider of (?:\w+[ ,-]+){0,4}?(managed care|physical therapy|health ?care|medical|"
    r"nursing|home health|rehabilitation|dental|behavioral health|mental health)\b)", re.I)
# makes things FOR other companies (contract manufacturer, CDMO, outsourced
# manufacturing) - a supplier, not a developer with its own research agenda
_CONTRACT = re.compile(
    r"contract manufactur|\bCDMO\b|\bCMO services\b|contract research and manufacturing|"
    r"contract (?:development(?: and|,)? |dosage form development and |process research and )"
    r"manufactur|outsourc\w* (?:\w+ ){0,3}manufactur|"
    r"end-to-end (?:\w+ ){0,3}(?:manufacturing|solutions from prototyping)|"
    r"(?:provides?|offering|offers) (?:\w+[ ,]+){0,5}manufacturing (?:and \w+ )?services|"
    r"private[- ]label manufactur|manufactures? (?:\w+ ){0,3}solutions for medical devices",
    re.I)
# a developer of its own products that ALSO sells manufacturing services
_DEVELOPER = re.compile(r"\b(develops?|developing|discovers|commercializ\w*)\b", re.I)
_VET = re.compile(r"\b(animal|veterinar\w*|pet care)\b", re.I)
_PARENT = re.compile(
    r"\boperates as (?:a |an )?(?:wholly[- ]owned )?subsidiary of\s+([^.;]+?)\s*(?:\.(?:\s|$)|;|$)",
    re.I)


def _opening(description) -> str:
    """The first 250 characters - where Capital IQ says what it IS. Not 'up
    to the first full stop': "Sanofi-Aventis U.S. LLC" has full stops."""
    d = "" if description is None else str(description).strip()
    return "" if d in ("-", "nan", "None") else d[:250]


def organisation_type(name, description) -> str:
    name = "" if name is None else str(name)
    opening = _opening(description)
    cm = _CONTRACT.search(opening)
    if cm:
        dev = _DEVELOPER.search(opening)
        if not (dev and dev.start() < cm.start()):
            return CONTRACT
    if _MAKER.search(opening):
        return COMPANY
    # an animal hospital is a veterinary clinic, not a health system
    if _VET.search(name) or _VET.search(opening):
        return CLINIC
    if _HOSP_NAME.search(name) or _HOSP_DESC.search(opening):
        return HOSPITAL
    if _CLIN_DESC.search(opening) or _CLIN_NAME.search(name):
        return CLINIC
    return COMPANY


def parent_company(description):
    """'CONMED Corporation' from '... operates as a subsidiary of CONMED
    Corporation.' - else None."""
    d = "" if description is None else str(description)
    m = _PARENT.search(d)
    if not m:
        return None
    parent = m.group(1).strip().rstrip(",")
    return parent or None
