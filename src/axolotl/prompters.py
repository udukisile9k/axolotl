import copy
import dataclasses
from enum import auto, Enum
from typing import List, Tuple, Any, Union

IGNORE_TOKEN_ID = -100


class AlpacaPrompter:
    prompt_input = "Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.\n\n### Instruction:\n{instruction}\n\n### Input:\n{input}\n\n### Response:\n"
    prompt_no_input = "Below is an instruction that describes a task. Write a response that appropriately completes the request.\n\n### Instruction:\n{instruction}\n\n### Response:\n"
    response_split = "### Response:"

    def build_prompt(
        self,
        instruction: str,
        input: Union[None, str] = None,
        output: Union[None, str] = None,
    ) -> str:
        # returns the full prompt from instruction and optional input
        # if a label (=response, =output) is provided, it's also appended.
        if input:
            res = self.prompt_input.format(instruction=instruction, input=input)
        else:
            res = self.prompt_no_input.format(instruction=instruction)
        if output:
            res = f"{res}{output}"
        return res

    def get_response(self, output: str) -> str:
        return output.split(self.response_split)[1].strip()


class GPTeacherPrompter(AlpacaPrompter):
    ...


class SeparatorStyle(Enum):
    """Different separator style."""

    SINGLE = auto()
    TWO = auto()
    DOLLY = auto()


# TODO clean this 💩 up
@dataclasses.dataclass
class Conversation:
    """A class that keeps all conversation history."""

    system: str
    roles: List[str]
    messages: List[List[str]]
    offset: int
    sep_style: SeparatorStyle = SeparatorStyle.SINGLE
    sep: str = "###"
    sep2: str = None

    def get_prompt(self):
        seps = [self.sep, self.sep2]
        ret = self.system + seps[0]
        for i, (role, message) in enumerate(self.messages):
            if message:
                ret += role + ": " + message + seps[i % 2]
            else:
                ret += role + ":"
        return ret

    def copy(self):
        return Conversation(
            system=self.system,
            roles=self.roles,
            messages=[[x, y] for x, y in self.messages],
            offset=self.offset,
            sep_style=self.sep_style,
            sep=self.sep,
            sep2=self.sep2,
        )

    def append_message(self, role, message):
        self.messages.append([role, message])


conv_vicuna_v1_1 = Conversation(
    system="A chat between a curious user and an artificial intelligence assistant. "
    "The assistant gives helpful, detailed, and polite answers to the user's questions.",
    roles=["USER", "ASSISTANT"],
    messages=[],
    offset=0,
    sep_style=SeparatorStyle.TWO,
    sep=" ",
    sep2="</s>",
)


class ShareGPTPrompter:
    def build_prompt(self, source, tokenizer):
        if len(source) < 2:
            # If there isn't a back and forth conversation, ignore it
            # also happens on the data splitting leaving empty conversations
            raise IndexError

        conv = conv_vicuna_v1_1.copy()
        roles = {"human": conv.roles[0], "gpt": conv.roles[1]}

        try:
            # Apply prompt templates
            if (
                source[0]["from"] not in roles
                or roles[source[0]["from"]] != conv.roles[0]
            ):
                # Skip the first one if it is not from human
                source = source[1:]
        except IndexError as e:
            # sometimes there is a bing or system chat
            raise e

        conv.messages = []
        for j, sentence in enumerate(source):
            role = roles[sentence["from"]]
            assert role == conv.roles[j % 2]
            conv.append_message(role, sentence["value"])
        conversation = conv.get_prompt()

        # Tokenize conversations
        tokenized_result = tokenizer(
            conversation,
            truncation=True,
            max_length=2048,  # FIXME
            padding=False,
            return_tensors=None,
        )
        target = copy.deepcopy(tokenized_result["input_ids"])

        # Mask targets
        sep = conv.sep + conv.roles[1] + ": "

        rounds = conversation.split(conv.sep2)
        cur_len = 1
        for i, rou in enumerate(rounds):
            if rou == "":
                break

            parts = rou.split(sep)
            if len(parts) != 2:
                break
            parts[0] += sep
            round_len = len(tokenizer(rou)["input_ids"])
            instruction_len = len(tokenizer(parts[0])["input_ids"]) - 2
            target[cur_len : cur_len + instruction_len] = [
                IGNORE_TOKEN_ID
            ] * instruction_len

            cur_len += round_len
        target[cur_len:] = [IGNORE_TOKEN_ID] * (len(target) - cur_len)
        attention_mask = [
            1 if x != tokenizer.pad_token_id else 0
            for x in tokenized_result["input_ids"]
        ]

        return dict(
            input_ids=tokenized_result["input_ids"],
            labels=target,
            attention_mask=attention_mask,
        )
