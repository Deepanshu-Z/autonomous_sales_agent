import json

path = r"d:\Users\Admin\Desktop\NEW_AUTOMATION\local_auto\data\global_rules.json"

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

policies = data.get("universal_course_policies", {})

policies["batch_change"] = {
    "allowed": True,
    "frequency_limit": "once a month",
    "allowed_during_first_month": True,
    "rule": "Students are permitted to change their batch timing or time slot once a month if their schedule changes. If they ever miss an individual class, it cannot be rescheduled or carried over, but full session recordings are provided in the Sensationz App to practice anytime at their own pace."
}

policies["flexible_timings"] = {
    "daily_flexible_timings_allowed": False,
    "fixed_batch_required": True,
    "batch_change_permitted": True,
    "frequency_limit": "once a month",
    "substitute_option": "Classes follow a fixed daily batch schedule, but students are permitted to change their batch timing once a month."
}

data["universal_course_policies"] = policies

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=4, ensure_ascii=False)

print("Successfully updated global_rules.json")

