from identity_retrieval import retrieve_identity

def build_prompt(user_input):
    identity_context = retrieve_identity(user_input)

    system_block = "CORE IDENTITY:\n"
    for p in identity_context:
        system_block += f"- {p}\n"

    final_prompt = f"{system_block}\nUSER:\n{user_input}"
    return final_prompt
