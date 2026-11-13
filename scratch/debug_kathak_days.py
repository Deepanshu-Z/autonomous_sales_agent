import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.conversation_handler import handle_incoming_message

async def test():
    history = [
        {"role": "user", "content": "Ms.Madhuri"},
        {"role": "user", "content": "I want see demo lecture of katthak"},
        {"role": "assistant", "content": "You can watch the demo lecture of Ms. Madhuri for Kathak here: https://youtu.be/t-XO2enPGwM. This will give you a glimpse of her teaching style and expertise.\n\nWhat do you think about the demo? Would you like to proceed with selecting a batch timing? 😊"},
        {"role": "user", "content": "I don't got it"},
        {"role": "assistant", "content": "No problem! You can watch the demo lecture of Ms. Madhuri for Kathak here: https://youtu.be/t-XO2enPGwM. It showcases her teaching style.\n\nWould you like to select a batch timing for your classes? 😊"},
        {"role": "user", "content": "I will le\nMujhee basic se padhna haii"},
        {"role": "assistant", "content": "Aapne bilkul sahi kaha, Kathak classes basic se shuru hoti hain. Ab humein aapka batch timing select karna hai. Available timings hain:\n\n• *Tuesday & Thursday:* 4:00 PM to 5:00 PM, 5:00 PM to 6:00 PM, 6:00 PM to 7:00 PM, 7:00 PM to 8:00 PM, 8:00 PM to 9:00 PM\n• *Wednesday & Friday:* 4:00 PM to 5:00 PM, 5:00 PM to 6:00 PM, 6:00 PM to 7:00 PM, 7:00 PM to 8:00 PM, 8:00 PM to 9:00 PM\n• *Saturday & Sunday:* 5:00 PM to 6:00 PM, 6:00 PM to 7:00 PM, 7:00 PM to 8:00 PM, 8:00 PM to 9:00 PM\n\nAap inme se kaunsa timing choose karna chahenge? 😊"},
        {"role": "user", "content": "6 pm to 7 pm"},
        {"role": "assistant", "content": "Aapne 6:00 PM se 7:00 PM ka batch select kiya hai. Ab humein aapke batch days select karne hain. Available days hain: *Tuesday/Thursday* ya *Wednesday/Friday*.\n\nAap inme se kaunsa day pair choose karna chahenge? 😊"}
    ]
    state = {
        "phone": "918799911415",
        "course": "kathak",
        "timing": "6:00 PM to 7:00 PM",
        "selected_timing": "6:00 PM to 7:00 PM",
        "teacher": "Ms. Madhuri",
        "selected_teacher": "Ms. Madhuri"
    }

    res = await handle_incoming_message(
        user_message="Sat nd sun",
        session_state=state,
        conversation_history=history,
        course_id="kathak"
    )
    print("REPLY:\n", res.get("reply"))
    print("\nFOLLOWUP:\n", res.get("followup"))
    print("\nNEW_STATE:\n", res.get("new_state"))

if __name__ == "__main__":
    asyncio.run(test())

