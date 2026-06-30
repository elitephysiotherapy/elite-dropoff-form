"""Synthetic tests — prove the engine obeys each rule before wiring it live."""
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(override=True)
import reactivations as R

IA = "382563815654429852"        # 1. Initial Appointment (an IA type)
FU = "382589431795684515"        # 4. Club Follow Up Appointment (not an IA)
NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)

def appt(aid, typ, start, created, cancelled=None, dna=False):
    return {"id": aid,
            "appointment_type": {"links": {"self": f"/appointment_types/{typ}"}},
            "patient": {"links": {"self": "/patients/1"}},
            "starts_at": start, "created_at": created,
            "cancelled_at": cancelled, "did_not_arrive": dna}

def count(hist):
    return sum(1 for r in R.reactivation_records(hist, NOW) if not r["is_new_booking"])

def newbookings(hist):
    return sum(1 for r in R.reactivation_records(hist, NOW) if r["is_new_booking"])

T = []
def check(name, got, want):
    T.append((name, got, want, got == want))

# 1. CNA then rebook NEXT day, no future appt -> 1 reactivation
check("CNA then next-day rebook = 1", count([
    appt("a", FU, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z", cancelled="2026-06-09T09:00:00Z"),
    appt("b", FU, "2026-06-20T09:00:00Z", "2026-06-10T10:00:00Z"),
]), 1)

# 2. SAME-day cancel-and-rebook -> reschedule -> 0
check("same-day cancel+rebook = 0", count([
    appt("a", FU, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z", cancelled="2026-06-09T09:00:00Z"),
    appt("b", FU, "2026-06-20T09:00:00Z", "2026-06-09T15:00:00Z"),
]), 0)

# 3. Cancel while still holding ANOTHER future appt -> 0
check("cancel while holding another future = 0", count([
    appt("a", FU, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z", cancelled="2026-06-05T09:00:00Z"),
    appt("b", FU, "2026-06-25T09:00:00Z", "2026-06-01T09:00:00Z"),
]), 0)

# 4. IADNR: attend IA, book nothing, then book follow-up days later -> 1
check("IADNR then later follow-up = 1", count([
    appt("a", IA, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z"),
    appt("b", FU, "2026-06-25T09:00:00Z", "2026-06-15T09:00:00Z"),
]), 1)

# 5. drop -> rebook -> drop -> rebook (NO attendance) = 1
check("drop-rebook-drop-rebook (no attend) = 1", count([
    appt("a", FU, "2026-06-05T09:00:00Z", "2026-06-01T09:00:00Z", cancelled="2026-06-04T09:00:00Z"),
    appt("b", FU, "2026-06-12T09:00:00Z", "2026-06-06T09:00:00Z", cancelled="2026-06-11T09:00:00Z"),
    appt("c", FU, "2026-06-22T09:00:00Z", "2026-06-13T09:00:00Z"),
]), 1)

# 6. drop -> rebook -> ATTEND -> drop -> rebook = 2
check("drop-rebook-attend-drop-rebook = 2", count([
    appt("a", FU, "2026-05-05T09:00:00Z", "2026-05-01T09:00:00Z", cancelled="2026-05-04T09:00:00Z"),
    appt("b", FU, "2026-05-12T09:00:00Z", "2026-05-06T09:00:00Z"),                       # attended (past)
    appt("c", FU, "2026-05-20T09:00:00Z", "2026-05-12T10:00:00Z", cancelled="2026-05-19T09:00:00Z"),
    appt("d", FU, "2026-06-02T09:00:00Z", "2026-05-21T09:00:00Z"),
]), 2)

# 7. CNA then NEW IA >60 days later -> new booking, NOT a reactivation
check("drop then new IA >60d = 0 reactivations", count([
    appt("a", FU, "2026-03-10T09:00:00Z", "2026-03-01T09:00:00Z", cancelled="2026-03-09T09:00:00Z"),
    appt("b", IA, "2026-06-20T09:00:00Z", "2026-06-10T09:00:00Z"),
]), 0)
check("drop then new IA >60d = 1 new booking", newbookings([
    appt("a", FU, "2026-03-10T09:00:00Z", "2026-03-01T09:00:00Z", cancelled="2026-03-09T09:00:00Z"),
    appt("b", IA, "2026-06-20T09:00:00Z", "2026-06-10T09:00:00Z"),
]), 1)

# 8. drop then FOLLOW-UP >60 days later -> still a reactivation
check("drop then follow-up >60d = 1 reactivation", count([
    appt("a", FU, "2026-03-10T09:00:00Z", "2026-03-01T09:00:00Z", cancelled="2026-03-09T09:00:00Z"),
    appt("b", FU, "2026-06-20T09:00:00Z", "2026-06-10T09:00:00Z"),
]), 1)

# 9. DNA same-day rebook -> reschedule -> 0
check("DNA same-day rebook = 0", count([
    appt("a", FU, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z", dna=True),
    appt("b", FU, "2026-06-20T09:00:00Z", "2026-06-10T15:00:00Z"),
]), 0)

# 10. Therapist/time change with NO cancellation (just attended) -> not a drop -> 0
check("no cancellation, active = 0", count([
    appt("a", FU, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z"),
    appt("b", FU, "2026-06-20T09:00:00Z", "2026-06-10T09:00:00Z"),
]), 0)

# 11. DNA then rebook a different (later) day, no future appt -> 1
check("DNA then later rebook = 1", count([
    appt("a", FU, "2026-06-10T09:00:00Z", "2026-06-01T09:00:00Z", dna=True),
    appt("b", FU, "2026-06-20T09:00:00Z", "2026-06-12T09:00:00Z"),
]), 1)

print("RESULT  test")
print("-" * 60)
allpass = True
for name, got, want, ok in T:
    allpass &= ok
    print(f"{'PASS' if ok else 'FAIL':5}   {name}   (got {got}, want {want})")
print("-" * 60)
print("ALL PASS" if allpass else "SOME FAILED")
