from __future__ import annotations

import copy
import json
import unittest

from data_asset_poc.task_verification.authority import FixtureKeyring, digest, verify_manifest, verify_target


NOW = "2026-11-05T12:00:00+00:00"
LATER = "2026-11-05T15:00:00+00:00"


def profile():
    value = {
        "task_id": "task-add-v1", "task_version": "1.0",
        "task_description": "controlled add task",
        "required_capabilities": [{"family": "arithmetic", "operation": "add"}],
        "expected_output": "json_integer",
        "evaluation_criteria": "exact_deterministic_answer",
        "task_source": "task_fixture",
    }
    return {**value, "task_profile_sha256": digest(value)}


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.keys = FixtureKeyring(identities={
            "target-a": {"role": "target_authority", "authorized_task_ids": ["task-add-v1"],
                         "authorized_task_versions": {"task-add-v1": ["1.0"]}},
            "source-a": {"role": "source_authority", "authorized_source_ids": ["release-a"],
                         "authorized_source_versions": {"release-a": ["2026.11"]}},
        })
        self.target = profile()
        self.target_envelope = self.keys.sign("target-a", {
            "kind": "TARGET_AUTHORIZATION", "caller_id": "target-a",
            "task_id": "task-add-v1", "task_version": "1.0",
            "profile_sha256": self.target["task_profile_sha256"],
            "issued_at": "2026-11-05T11:00:00+00:00", "expires_at": "2026-11-05T14:00:00+00:00",
        })
        self.records = [
            {"sample_id": "m1", "source_id": "release-a", "asset_version": "2026.11", "source_version": "1",
             "instruction": "Add 2 and 3", "response": "5"},
            {"sample_id": "m2", "source_id": "release-a", "asset_version": "2026.11", "source_version": "1",
             "instruction": "Add 4 and 5", "response": "9"},
        ]
        base = {"source_id": "release-a", "asset_version": "2026.11", "source_version": "1", "members": [
            {"sample_id": r["sample_id"], "member_sha256": digest(r)} for r in self.records]}
        self.manifest = {**base, "manifest_sha256": digest(base)}
        self.manifest_envelope = self.keys.sign("source-a", {
            "kind": "SOURCE_MANIFEST_AUTHORIZATION", "caller_id": "source-a",
            "source_id": "release-a", "asset_version": "2026.11",
            "manifest_sha256": self.manifest["manifest_sha256"],
            "issued_at": "2026-11-05T11:00:00+00:00", "expires_at": "2026-11-05T14:00:00+00:00",
        })

    def test_valid_target_separates_all_fields(self):
        out = verify_target(self.target, self.target_envelope, self.keys.registry, NOW)
        self.assertEqual(out["decision"], True)
        self.assertEqual((out["integrity"], out["identity"], out["permission"], out["validity"]), (True, True, True, True))

    def test_correct_hash_forged_profile_is_rejected(self):
        forged = copy.deepcopy(self.target)
        forged["task_description"] = "unauthorized description"
        forged["task_profile_sha256"] = digest({k: v for k, v in forged.items() if k != "task_profile_sha256"})
        # A genuine new hash does not match the authority's signed binding.
        out = verify_target(forged, self.target_envelope, self.keys.registry, NOW)
        self.assertFalse(out["decision"])
        self.assertTrue(out["integrity"])
        self.assertIn("PROFILE_BINDING_MISMATCH", out["reasons"])
        # Forging the envelope payload without the authority key invalidates signature.
        env = copy.deepcopy(self.target_envelope)
        env["payload"]["profile_sha256"] = forged["task_profile_sha256"]
        out = verify_target(forged, env, self.keys.registry, NOW)
        self.assertEqual(out["decision"], 0)
        self.assertIn("INVALID_SIGNATURE", out["reasons"])

    def test_pinned_profile_cannot_be_replaced_even_by_authorized_signer(self):
        pinned = copy.deepcopy(self.keys.registry)
        pinned["identities"]["target-a"]["authorized_task_profile_hashes"] = {
            "task-add-v1": {"1.0": self.target["task_profile_sha256"]}}
        forged = copy.deepcopy(self.target)
        forged["task_description"] = "different signed content under same version"
        forged["task_profile_sha256"] = digest({k: v for k, v in forged.items() if k != "task_profile_sha256"})
        payload = dict(self.target_envelope["payload"], profile_sha256=forged["task_profile_sha256"])
        env = self.keys.sign("target-a", payload)
        out = verify_target(forged, env, pinned, NOW)
        self.assertTrue(out["identity"])
        self.assertTrue(out["integrity"])
        self.assertEqual(out["decision"], 0)
        self.assertIn("PROFILE_HASH_NOT_PINNED", out["reasons"])

    def test_authorized_caller_signing_unregistered_target_or_version_is_denied(self):
        for task_id, task_version, expected_reason in (
            ("task-subtract-v1", "1.0", "TASK_NOT_AUTHORIZED"),
            ("task-add-v1", "2.0", "TASK_VERSION_NOT_AUTHORIZED"),
        ):
            with self.subTest(task_id=task_id, version=task_version):
                forged = dict(self.target, task_id=task_id, task_version=task_version)
                forged["task_profile_sha256"] = digest({k: v for k, v in forged.items() if k != "task_profile_sha256"})
                payload = dict(self.target_envelope["payload"], task_id=task_id, task_version=task_version,
                               profile_sha256=forged["task_profile_sha256"])
                out = verify_target(forged, self.keys.sign("target-a", payload), self.keys.registry, NOW)
                self.assertTrue(out["identity"])
                self.assertTrue(out["integrity"])
                self.assertEqual(out["decision"], 0)
                self.assertIn(expected_reason, out["reasons"])

    def test_target_version_revocation_and_absent_version_registry(self):
        for changes, expected_reason in (
            ({"revoked_task_versions": {"task-add-v1": ["1.0"]}}, "TASK_VERSION_REVOKED"),
            ({"authorized_task_versions": {}}, "TASK_VERSION_NOT_AUTHORIZED"),
            ({"authorized_task_versions": None}, "TASK_NOT_AUTHORIZED"),
        ):
            with self.subTest(changes=changes):
                registry = copy.deepcopy(self.keys.registry)
                registry["identities"]["target-a"].update(changes)
                out = verify_target(self.target, self.target_envelope, registry, NOW)
                self.assertEqual(out["decision"], 0)
                self.assertIn(expected_reason, out["reasons"])

    def test_future_issued_target_and_caller_role_forgery(self):
        future = dict(self.target_envelope["payload"], issued_at="2026-11-05T12:30:00+00:00")
        out = verify_target(self.target, self.keys.sign("target-a", future), self.keys.registry, NOW)
        self.assertEqual(out["decision"], 0)
        self.assertIn("AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID", out["reasons"])
        # Source signer is real and signs correctly, but its registered role differs.
        source_payload = dict(self.target_envelope["payload"], caller_id="source-a")
        out = verify_target(self.target, self.keys.sign("source-a", source_payload), self.keys.registry, NOW)
        self.assertEqual(out["decision"], 0)
        self.assertIn("WRONG_SIGNER_ROLE", out["reasons"])

    def test_signature_cannot_be_reassigned_or_version_changed(self):
        env = copy.deepcopy(self.target_envelope)
        env["signer_id"] = "source-a"
        out = verify_target(self.target, env, self.keys.registry, NOW)
        self.assertFalse(out["decision"])
        changed = copy.deepcopy(self.target)
        changed["task_version"] = "2.0"
        changed["task_profile_sha256"] = digest({k: v for k, v in changed.items() if k != "task_profile_sha256"})
        out2 = verify_target(changed, self.target_envelope, self.keys.registry, NOW)
        self.assertFalse(out2["decision"])
        self.assertIn("TARGET_VERSION_MISMATCH", out2["reasons"])

    def test_unknown_caller_is_unknown_without_identity_claim(self):
        env = copy.deepcopy(self.target_envelope)
        env["signer_id"] = "forged-caller"
        out = verify_target(self.target, env, self.keys.registry, NOW)
        self.assertIsNone(out["decision"])
        self.assertIsNone(out["identity"])
        self.assertIn("UNKNOWN_SIGNER", out["reasons"])

    def test_expiry_revocation_and_unauthorized_task(self):
        expired = verify_target(self.target, self.target_envelope, self.keys.registry, LATER)
        self.assertFalse(expired["decision"])
        revoked = copy.deepcopy(self.keys.registry)
        revoked["identities"]["target-a"]["status"] = "REVOKED"
        self.assertFalse(verify_target(self.target, self.target_envelope, revoked, NOW)["decision"])
        denied = copy.deepcopy(self.keys.registry)
        denied["identities"]["target-a"]["authorized_task_ids"] = []
        self.assertFalse(verify_target(self.target, self.target_envelope, denied, NOW)["decision"])

    def test_manifest_is_metadata_only_and_member_tamper_fails(self):
        out = verify_manifest(self.manifest, self.manifest_envelope, self.keys.registry, self.records, NOW)
        self.assertTrue(out["decision"])
        self.assertEqual(out["affects"], "E_ONLY")
        self.assertIsNone(out.get("content_correctness"))
        tampered = copy.deepcopy(self.records)
        tampered[0]["response"] = "6"
        out2 = verify_manifest(self.manifest, self.manifest_envelope, self.keys.registry, tampered, NOW)
        self.assertFalse(out2["decision"])
        self.assertIn("MEMBER_MANIFEST_MISMATCH", out2["reasons"])

    def test_manifest_hash_and_source_permission_fail_closed(self):
        bad = copy.deepcopy(self.manifest)
        bad["members"][0]["member_sha256"] = "0" * 64
        out = verify_manifest(bad, self.manifest_envelope, self.keys.registry, self.records, NOW)
        self.assertFalse(out["decision"])
        denied = copy.deepcopy(self.keys.registry)
        denied["identities"]["source-a"]["authorized_source_ids"] = []
        out2 = verify_manifest(self.manifest, self.manifest_envelope, denied, self.records, NOW)
        self.assertFalse(out2["decision"])
        self.assertIn("SOURCE_NOT_AUTHORIZED", out2["reasons"])

    def test_source_version_revocation_and_signed_unregistered_version(self):
        revoked = copy.deepcopy(self.keys.registry)
        revoked["identities"]["source-a"]["revoked_source_versions"] = {"release-a": ["2026.11"]}
        out = verify_manifest(self.manifest, self.manifest_envelope, revoked, self.records, NOW)
        self.assertEqual(out["decision"], 0)
        self.assertIn("SOURCE_VERSION_REVOKED", out["reasons"])
        changed = dict(self.manifest, asset_version="2026.12")
        changed["manifest_sha256"] = digest({k: v for k, v in changed.items() if k != "manifest_sha256"})
        payload = dict(self.manifest_envelope["payload"], asset_version="2026.12",
                       manifest_sha256=changed["manifest_sha256"])
        out = verify_manifest(changed, self.keys.sign("source-a", payload), self.keys.registry, self.records, NOW)
        self.assertTrue(out["identity"])
        self.assertEqual(out["decision"], 0)
        self.assertIn("SOURCE_VERSION_NOT_AUTHORIZED", out["reasons"])

    def test_valid_signature_and_hash_cannot_reassign_member_source(self):
        wrong = copy.deepcopy(self.records)
        wrong[0]["source_id"] = "release-other"
        base = {k: v for k, v in self.manifest.items() if k not in ("manifest_sha256", "members")}
        base["members"] = [{"sample_id": r["sample_id"], "member_sha256": digest(r)} for r in wrong]
        manifest = {**base, "manifest_sha256": digest(base)}
        payload = dict(self.manifest_envelope["payload"], manifest_sha256=manifest["manifest_sha256"])
        out = verify_manifest(manifest, self.keys.sign("source-a", payload), self.keys.registry, wrong, NOW)
        self.assertTrue(out["identity"])
        self.assertEqual(out["decision"], 0)
        self.assertIn("MEMBER_MANIFEST_MISMATCH", out["reasons"])

    def test_malformed_inputs_do_not_certify_or_throw(self):
        bad_profiles = [None, {}, {"task_id": [], "task_version": "1", "task_profile_sha256": "x"},
                        dict(self.target, unexpected=float("nan")),
                        dict(self.target, unexpected="\ud800")]
        for bad in bad_profiles:
            with self.subTest(profile=repr(bad)):
                out = verify_target(bad, self.target_envelope, self.keys.registry, NOW)
                self.assertNotEqual(out["decision"], 1)
        for env, registry in (({}, self.keys.registry), (self.target_envelope, {}),
                              (dict(self.target_envelope, signature="!!!"), self.keys.registry)):
            with self.subTest(envelope=repr(env), registry=repr(registry)):
                self.assertNotEqual(verify_target(self.target, env, registry, NOW)["decision"], 1)
        for manifest, records in ((None, self.records), (self.manifest, None),
                                  (self.manifest, self.records + self.records[:1])):
            with self.subTest(manifest=repr(manifest), records=repr(records)):
                self.assertNotEqual(verify_manifest(manifest, self.manifest_envelope,
                                                    self.keys.registry, records, NOW)["decision"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

