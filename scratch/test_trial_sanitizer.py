import re

clean_followup = "Kya aap trial class book karna chahenge ya aur koi sawaal hai? 😊"
clean_reply = "Maine hamari live class ka practice video upar send kar diya hai! 🎬 Aap is video ko dekh sakte hain taaki aapko hamari teaching style ka idea mil sake."
is_trial_unlocked = False

print("Regex match in followup:", bool(re.search(r"\b(trial|trials|ट्रायल)\b", clean_followup, re.IGNORECASE)))
if not is_trial_unlocked:
    if clean_followup and re.search(r"\b(trial|trials|ट्रायल)\b", clean_followup, re.IGNORECASE):
        print("MATCHED! Replacing clean_followup")
        clean_followup = "Aap mostly subah free hote hain ya evening mein practice karna convenient rahega? 😊"

print("Result clean_followup:", clean_followup)
