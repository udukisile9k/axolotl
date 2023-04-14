import abc

from transformers import PreTrainedTokenizer

IGNORE_INDEX = -100
LLAMA_DEFAULT_PAD_TOKEN = "[PAD]"
LLAMA_DEFAULT_EOS_TOKEN = "</s>"
LLAMA_DEFAULT_BOS_TOKEN = "<s>"
LLAMA_DEFAULT_UNK_TOKEN = "<unk>"


class InvalidDataException(Exception):
    pass


class PromptTokenizingStrategy(abc.ABC):
    def __init__(
        self,
        prompter,
        tokenizer,
        train_on_inputs: bool = False,
        sequence_len: int = 2048,
    ):
        self.prompter = prompter
        self.tokenizer: PreTrainedTokenizer = tokenizer
        self.train_on_inputs = train_on_inputs
        self.sequence_len = sequence_len

    @abc.abstractmethod
    def tokenize_prompt(self, prompt):
        pass


class AlpacaPromptTokenizingStrategy(PromptTokenizingStrategy):
    def tokenize_prompt(self, prompt):
        full_prompt = self._tokenize_full_prompt(prompt)
        tokenized_full_prompt = self._tokenize(full_prompt)
        if not self.train_on_inputs:
            user_prompt = self.prompter.build_prompt(
                prompt["instruction"], prompt["input"]
            )
            tokenized_user_prompt = self._tokenize(user_prompt, add_eos_token=False)
            user_prompt_len = len(tokenized_user_prompt["input_ids"])
            # TODO this could be sped up using numpy array slicing
            tokenized_full_prompt["labels"] = [
                -100
            ] * user_prompt_len + tokenized_full_prompt["labels"][user_prompt_len:]

        return tokenized_full_prompt

    def _tokenize_full_prompt(self, prompt):
        return self.prompter.build_prompt(
            prompt["instruction"],
            prompt["input"],
            prompt["output"],
        )

    def _tokenize(self, prompt, add_eos_token=True):
        result = self.tokenizer(
            prompt,
            truncation=True,
            max_length=self.sequence_len,
            padding=False,
            return_tensors=None,
        )
        if (
            result["input_ids"][-1] != self.tokenizer.eos_token_id
            and len(result["input_ids"]) < self.sequence_len
            and add_eos_token
        ):
            result["input_ids"].append(self.tokenizer.eos_token_id)
            result["attention_mask"].append(1)

        result["labels"] = result["input_ids"].copy()
        return result


class GPTeacherPromptTokenizingStrategy(AlpacaPromptTokenizingStrategy):
    def _tokenize_full_prompt(self, prompt):
        return self.prompter.build_prompt(
            prompt["instruction"],
            prompt["input"],
            prompt["response"],
        )


class ShareGPTPromptTokenizingStrategy(PromptTokenizingStrategy):
    def tokenize_prompt(self, prompt):
        try:
            return self.prompter.build_prompt(prompt["conversations"], self.tokenizer)
        except (KeyError, AssertionError) as e:
            raise InvalidDataException(str(e))
