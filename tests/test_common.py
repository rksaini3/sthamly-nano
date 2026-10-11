    def test_length_limits(self):
        a96 = "a" * 96
        self.assertEqual(c.normalize_model_id(f"{a96}/b"), f"{a96}/b")
        self.assertEqual(c.normalize_model_id(f"{a96}/{a96}"), f"{a96}/{a96}")
        self.assertIsNone(c.normalize_model_id("a" * 97 + "/b"))
        self.assertIsNone(c.normalize_model_id("a/" + "b" * 97))
