"""Tests for clean_clinicaltrials.py - ClinicalTrials.gov API pages to one
row per trial per company.

Each test writes made-up API pages (same nesting as the real v2 API) into a
temp folder and cleans them. No real data, no network.
"""
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from clean_clinicaltrials import OUT_COLUMNS, clean, phase_of


def trial(nct, lead=("Acme Bio, Inc.", "INDUSTRY"), collaborators=(),
          status="RECRUITING", phases=("PHASE2",), start="2025-03-01",
          conditions=("Type 2 Diabetes",), interventions=("AB-101",),
          sites=(("UConn Health", "Farmington", "Connecticut", "United States"),),
          study_type="INTERVENTIONAL", officials=None):
    ps = {
        "identificationModule": {"nctId": nct, "briefTitle": f"Trial {nct}"},
        "statusModule": {"overallStatus": status,
                         "startDateStruct": {"date": start},
                         "completionDateStruct": {"date": "2027-12"},
                         "lastUpdatePostDateStruct": {"date": "2026-08-01"}},
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": lead[0], "class": lead[1]},
            "collaborators": [{"name": n, "class": c} for n, c in collaborators]},
        "designModule": {"studyType": study_type, "phases": list(phases),
                         "enrollmentInfo": {"count": 120}},
        "conditionsModule": {"conditions": list(conditions)},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": i}
                                                      for i in interventions]},
        "contactsLocationsModule": {"locations": [
            {"facility": f, "city": c, "state": s, "country": k} for f, c, s, k in sites]},
    }
    if officials:          # never requested by the fetch - must never leak through
        ps["contactsLocationsModule"]["overallOfficials"] = [{"name": officials}]
    return {"protocolSection": ps}


def run(*pages, gz=True):
    root = Path(tempfile.mkdtemp(prefix="nexusai_ctgov_"))
    for i, studies in enumerate(pages, 1):
        body = {"studies": list(studies), "nextPageToken": "x"}
        if gz:
            with gzip.open(root / f"page_{i:05d}.json.gz", "wt", encoding="utf-8") as fh:
                json.dump(body, fh)
        else:
            (root / f"page_{i:05d}.json").write_text(json.dumps(body))
    return clean(root)


class TestRows(unittest.TestCase):

    def test_columns_and_one_row(self):
        df = run([trial("NCT1")])
        self.assertEqual(list(df.columns), OUT_COLUMNS)
        self.assertEqual(len(df), 1)
        r = df.iloc[0]
        self.assertEqual((r.company, r.role, r.nct_id), ("Acme Bio, Inc.", "lead sponsor", "NCT1"))

    def test_one_row_per_company_on_the_trial(self):
        df = run([trial("NCT1", collaborators=[("Beta Pharma LLC", "INDUSTRY")])])
        self.assertEqual(sorted(zip(df.company, df.role)),
                         [("Acme Bio, Inc.", "lead sponsor"), ("Beta Pharma LLC", "collaborator")])
        self.assertEqual(df.set_index("company").at["Acme Bio, Inc.", "other_companies"],
                         "Beta Pharma LLC")

    def test_company_as_collaborator_on_university_trial(self):
        """The tie we most want: a university trial a company helps fund."""
        df = run([trial("NCT1", lead=("University of Connecticut", "OTHER"),
                        collaborators=[("Acme Bio, Inc.", "INDUSTRY")])])
        r = df.iloc[0]
        self.assertEqual((r.company, r.role, r.partners),
                         ("Acme Bio, Inc.", "collaborator", "University of Connecticut"))

    def test_trial_without_a_company_dropped(self):
        df = run([trial("NCT1", lead=("Yale University", "OTHER"),
                        collaborators=[("NIH", "NIH")])])
        self.assertEqual(len(df), 0)

    def test_duplicate_trial_across_pages_counted_once(self):
        df = run([trial("NCT1")], [trial("NCT1")])
        self.assertEqual(len(df), 1)

    def test_plain_json_pages_also_read(self):
        self.assertEqual(len(run([trial("NCT1")], gz=False)), 1)

    def test_no_pages_is_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            clean(Path(tempfile.mkdtemp()))


class TestPartners(unittest.TestCase):

    def test_universities_and_hospitals_are_partners_nih_is_not(self):
        df = run([trial("NCT1", collaborators=[("Yale University", "OTHER"),
                                               ("Hartford Hospital", "OTHER"),
                                               ("National Cancer Institute (NCI)", "NIH")])])
        self.assertEqual(df.iloc[0].partners, "Yale University; Hartford Hospital")

    def test_individual_doctor_as_sponsor_not_kept(self):
        """ClinicalTrials.gov files a sponsor-investigator as OTHER, like a
        university. Only names with an institution word are kept."""
        df = run([trial("NCT1", lead=("Jane Example", "OTHER"),
                        collaborators=[("Acme Bio, Inc.", "INDUSTRY"),
                                       ("UConn Health", "OTHER")])])
        self.assertEqual(df.iloc[0].partners, "UConn Health")

    def test_unnamed_connecticut_site(self):
        df = run([trial("NCT1", sites=[(None, "Hartford", "Connecticut", "United States")])])
        self.assertEqual(df.iloc[0].ct_sites, "unnamed site (Hartford)")

    def test_no_partner_is_blank(self):
        self.assertTrue(run([trial("NCT1")]).iloc[0].partners in ("", None))


class TestStatus(unittest.TestCase):

    def test_running_statuses(self):
        for s in ("RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",
                  "ENROLLING_BY_INVITATION"):
            self.assertTrue(run([trial("NCT1", status=s)]).iloc[0].is_active, s)

    def test_ended_statuses(self):
        for s in ("COMPLETED", "TERMINATED", "SUSPENDED", "UNKNOWN", "WITHDRAWN"):
            self.assertFalse(run([trial("NCT1", status=s)]).iloc[0].is_active, s)

    def test_expanded_access_is_not_a_trial(self):
        self.assertEqual(len(run([trial("NCT1", status="AVAILABLE")])), 0)


class TestFields(unittest.TestCase):

    def test_phase_labels(self):
        self.assertEqual(phase_of(["PHASE2"]), ("Phase 2", 2))
        self.assertEqual(phase_of(["PHASE1", "PHASE2"]), ("Phase 1/2", 2))
        self.assertEqual(phase_of(["NA"]), ("Not applicable", None))
        self.assertEqual(phase_of(["EARLY_PHASE1"]), ("Early Phase 1", 1))
        self.assertEqual(phase_of([]), (None, None))
        self.assertEqual(phase_of(None), (None, None))

    def test_dates_and_start_year(self):
        r = run([trial("NCT1", start="2025-03")]).iloc[0]
        self.assertEqual((r.start_date, r.start_year), ("2025-03", 2025))
        self.assertEqual(r.completion_date, "2027-12")

    def test_conditions_and_treatments(self):
        r = run([trial("NCT1", conditions=("Obesity", "Type 2 Diabetes", "obesity"),
                       interventions=("AB-101", "Placebo"))]).iloc[0]
        self.assertEqual(r.conditions, "Obesity; Type 2 Diabetes")      # repeat dropped
        self.assertEqual(r.interventions, "AB-101; Placebo")
        self.assertEqual(r.intervention_types, "DRUG")

    def test_sites_counted_and_connecticut_listed(self):
        r = run([trial("NCT1", sites=[("UConn Health", "Farmington", "Connecticut", "United States"),
                                      ("Yale New Haven Hospital", "New Haven", "Connecticut", "United States"),
                                      ("Mass General", "Boston", "Massachusetts", "United States"),
                                      ("Charite", "Berlin", None, "Germany")])]).iloc[0]
        self.assertEqual((r.sites_total, r.sites_us, r.sites_ct), (4, 3, 2))
        self.assertEqual(r.ct_sites, "UConn Health (Farmington); Yale New Haven Hospital (New Haven)")

    def test_no_sites(self):
        r = run([trial("NCT1", sites=())]).iloc[0]
        self.assertEqual((r.sites_total, r.sites_ct), (0, 0))

    def test_no_person_named_in_output(self):
        df = run([trial("NCT1", officials="Dr Jane Example")])
        self.assertFalse(df.astype(str).apply(lambda c: c.str.contains("Jane Example")).any().any())


class TestFetch(unittest.TestCase):
    """fetch_clinicaltrials.py with the network call replaced."""

    def setUp(self):
        import fetch_clinicaltrials as F
        self.F = F
        self.root = Path(tempfile.mkdtemp())
        self._saved = (F.OUT_DIR, F.get_page, F.PAUSE)
        F.OUT_DIR, F.PAUSE = self.root / "clinicaltrials", 0

    def tearDown(self):
        self.F.OUT_DIR, self.F.get_page, self.F.PAUSE = self._saved

    def test_pages_saved_and_readable_by_clean(self):
        pages = [{"studies": [trial("NCT1")], "nextPageToken": "t2", "totalCount": 2},
                 {"studies": [trial("NCT2")]}]
        self.F.get_page = lambda token, size, count_total: pages[0 if token is None else 1]
        import sys
        argv, sys.argv = sys.argv, ["fetch_clinicaltrials.py"]
        try:
            self.F.main()
        finally:
            sys.argv = argv
        self.assertEqual(len(list(self.F.OUT_DIR.glob("page_*.json.gz"))), 2)
        self.assertEqual(sorted(clean(self.F.OUT_DIR).nct_id), ["NCT1", "NCT2"])

    def test_failed_download_keeps_the_old_pages(self):
        self.F.OUT_DIR.mkdir(parents=True)
        (self.F.OUT_DIR / "page_00001.json").write_text(json.dumps({"studies": [trial("OLD")]}))
        def boom(*a, **k):
            raise RuntimeError("network down")
        self.F.get_page = boom
        import sys
        argv, sys.argv = sys.argv, ["fetch_clinicaltrials.py"]
        try:
            with self.assertRaises(RuntimeError):
                self.F.main()
        finally:
            sys.argv = argv
        self.assertEqual(list(clean(self.F.OUT_DIR).nct_id), ["OLD"])

    def test_no_person_fields_requested(self):
        for f in self.F.FIELDS:
            self.assertNotRegex(f, "Official|Contact|Investigator|ResponsibleParty")


if __name__ == "__main__":
    unittest.main()
