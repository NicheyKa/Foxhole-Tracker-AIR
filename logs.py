from datetime import datetime, timezone

def log_edit(
    cursor,
    war_id: int,
    editor_id: int,
    target_id: int,
    vehicle: str,
    display_name: str,
    delta: int,
    before_count: int,
    after_count: int,
    points_delta: int
):
    cursor.execute(
        """
        INSERT INTO edit_logs (
            war_id, editor_id, target_id,
            vehicle, display_name,
            delta, before_count, after_count,
            points_delta, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            war_id,
            editor_id,
            target_id,
            vehicle,
            display_name,
            delta,
            before_count,
            after_count,
            points_delta,
            datetime.now(timezone.utc).isoformat()
        )
    )