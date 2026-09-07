import io
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.bulk_xml import parse_bulk_xml_stream

XML_NS = "http://apply.grants.gov/system/OpportunityDetail-V1.0"

SAMPLE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<Grants xmlns="{XML_NS}">
    <OpportunitySynopsisDetail_1_0>
        <OpportunityID>111111</OpportunityID>
        <OpportunityTitle>Robotic Prosthetics Research Program</OpportunityTitle>
        <CloseDate>12312099</CloseDate>
        <Description>Funding for advanced robotic prosthetics research.</Description>
        <AwardCeiling>500000</AwardCeiling>
    </OpportunitySynopsisDetail_1_0>
    <OpportunitySynopsisDetail_1_0>
        <OpportunityID>222222</OpportunityID>
        <OpportunityTitle>Community Arts Grant</OpportunityTitle>
        <CloseDate>12312099</CloseDate>
        <Description>Funding for community arts projects, no engineering content.</Description>
    </OpportunitySynopsisDetail_1_0>
    <OpportunitySynopsisDetail_1_0>
        <OpportunityID>333333</OpportunityID>
        <OpportunityTitle>Old Robotics Program (expired)</OpportunityTitle>
        <CloseDate>01011999</CloseDate>
        <Description>An old, long-closed robotics funding program.</Description>
    </OpportunitySynopsisDetail_1_0>
    <OpportunitySynopsisDetail_1_0>
        <OpportunityID>444444</OpportunityID>
        <OpportunityTitle>Rolling Drone Research Fund</OpportunityTitle>
        <Description>Rolling applications for autonomous drone research.</Description>
    </OpportunitySynopsisDetail_1_0>
</Grants>
"""


class TestBulkXmlAdapter(unittest.TestCase):
    def test_keyword_filter_and_date_filter(self):
        items = parse_bulk_xml_stream(io.BytesIO(SAMPLE_XML.encode("utf-8")))
        titles = {i["title"] for i in items}
        self.assertIn("Robotic Prosthetics Research Program", titles)
        self.assertIn("Rolling Drone Research Fund", titles)
        # No robotics keyword -> excluded.
        self.assertNotIn("Community Arts Grant", titles)
        # Robotics keyword present but deadline long past -> excluded.
        self.assertNotIn("Old Robotics Program (expired)", titles)

    def test_url_and_prefilled_fields(self):
        items = parse_bulk_xml_stream(io.BytesIO(SAMPLE_XML.encode("utf-8")))
        robotics = next(i for i in items if i["title"] == "Robotic Prosthetics Research Program")
        self.assertEqual(robotics["url"], "https://www.grants.gov/search-results-detail/111111")
        self.assertEqual(robotics["prefilled"]["deadline_date"], "2099-12-31")
        self.assertEqual(robotics["prefilled"]["deadline_confidence"], "explicit")
        self.assertEqual(robotics["prefilled"]["money_raw"], "$500000")

    def test_rolling_no_close_date_kept_with_no_deadline(self):
        items = parse_bulk_xml_stream(io.BytesIO(SAMPLE_XML.encode("utf-8")))
        rolling = next(i for i in items if i["title"] == "Rolling Drone Research Fund")
        self.assertIsNone(rolling["prefilled"]["deadline_date"])
        self.assertEqual(rolling["prefilled"]["deadline_confidence"], "none")


if __name__ == "__main__":
    unittest.main()
