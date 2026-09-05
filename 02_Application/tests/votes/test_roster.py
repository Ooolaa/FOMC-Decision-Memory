import unittest

from decision_memory.roster import (
    parse_policy_attendance,
    parse_policy_attendance_html,
)


class RosterParserTests(unittest.TestCase):
    def test_attendance_separates_members_alternates_and_other_presidents(self):
        blocks = [
            ["Introductory paragraph"],
            [
                "PRESENT:",
                "Jerome H. Powell, Chair",
                "John C. Williams, Vice Chair",
                "Michelle W. Bowman",
            ],
            [
                "James Bullard, Esther L. George, and Eric Rosengren, Alternate Members of the Federal Open Market Committee"
            ],
            [
                "Patrick Harker and Neel Kashkari, Presidents of the Federal Reserve Banks of Philadelphia and Minneapolis, respectively"
            ],
            ["James A. Clouse, Secretary", "Trevor A. Reeve, Economist"],
        ]

        participants = parse_policy_attendance(blocks)

        self.assertEqual(len(participants), 8)
        by_name = {item["display_name"]: item for item in participants}
        self.assertEqual(by_name["Jerome H. Powell"]["role"], "chair")
        self.assertTrue(by_name["Jerome H. Powell"]["is_chair"])
        self.assertEqual(by_name["John C. Williams"]["role"], "vice_chair")
        self.assertEqual(by_name["James Bullard"]["role"], "alternate_member")
        self.assertEqual(by_name["Patrick Harker"]["role"], "reserve_bank_president")
        self.assertNotIn("James A. Clouse", by_name)

    def test_attendance_requires_exactly_one_chair(self):
        with self.assertRaisesRegex(ValueError, "one Chair"):
            parse_policy_attendance(
                [["Attendance", "John C. Williams, Vice Chair"]]
            )

    def test_present_heading_may_be_a_separate_paragraph(self):
        participants = parse_policy_attendance(
            [
                ["PRESENT:"],
                ["Jerome H. Powell, Chair", "John C. Williams, Vice Chair"],
                ["James Bullard, Alternate Members of the Committee"],
                ["James A. Clouse, Secretary"],
            ]
        )

        self.assertEqual(
            [item["display_name"] for item in participants],
            ["Jerome H. Powell", "John C. Williams", "James Bullard"],
        )

    def test_legacy_table_attendance_resolves_honorific_surnames(self):
        content = b"""
        <strong>Present:</strong>
        <table><tr><td>
          Mr. Greenspan, Chairman<br>
          Mr. Geithner, Vice Chairman<br>
          Ms. Yellen<br>
        </td></tr></table>
        <p>Mses. Cumming and Minehan, Alternate Members of the Federal Open Market Committee</p>
        <p>Messrs. Fisher and Santomero, Presidents of the Federal Reserve Banks</p>
        <p>Mr. Reinhart, Secretary and Economist</p>
        """
        resolver = {
            "greenspan": "Alan Greenspan",
            "geithner": "Timothy F. Geithner",
            "yellen": "Janet L. Yellen",
            "cumming": "Christine M. Cumming",
            "minehan": "Cathy E. Minehan",
            "fisher": "Richard W. Fisher",
            "santomero": "Anthony M. Santomero",
        }

        participants = parse_policy_attendance_html(
            content,
            surname_resolver=resolver,
        )

        by_name = {item["display_name"]: item for item in participants}
        self.assertEqual(len(participants), 7)
        self.assertTrue(by_name["Alan Greenspan"]["is_chair"])
        self.assertEqual(by_name["Christine M. Cumming"]["role"], "alternate_member")
        self.assertEqual(
            by_name["Anthony M. Santomero"]["role"],
            "reserve_bank_president",
        )


if __name__ == "__main__":
    unittest.main()
