"""Human review step (R08 / Principle 04): a draft is frozen only when approved."""
from __future__ import annotations

from .config import get_config
from .db import Db
from .trace import record


def set_review(codebook_id: str, version: int, decision: str, reviewer: str,
               db: Db | None = None) -> str:
    assert decision in ("approved", "rejected")
    db = db or Db(secret_arn=get_config().resolve_secret_arn)
    db.execute(
        "UPDATE codebook_version SET status = :s, reviewed_by = :b, reviewed_at = now() "
        "WHERE codebook_id = :i AND codebook_version = :v",
        {"s": decision, "b": reviewer, "i": codebook_id, "v": version},
    )
    db.execute(
        "UPDATE codebook SET review_status = :s, reviewed_by = :b, reviewed_at = now() "
        "WHERE codebook_id = :i AND codebook_version = :v",
        {"s": decision, "b": reviewer, "i": codebook_id, "v": version},
    )
    record("pipeline", "review", codebook_id=codebook_id, version=version,
           decision=decision, reviewer=reviewer)
    return decision
