import logging
import os
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

import torch
import transformers
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    PreTrainedModel,
    AutoConfig,
)

try:
    from transformers import (
        LlamaForCausalLM,
        LlamaTokenizer,
    )
except:
    logging.warning(
        "This version of transformers does not support Llama. Consider upgrading."
    )

from axolotl.prompt_tokenizers import LLAMA_DEFAULT_PAD_TOKEN

if TYPE_CHECKING:
    from peft import PeftModel, PeftConfig
    from attrdict import AttrDefault
    from transformers import PreTrainedTokenizer


def load_model(
    base_model,
    base_model_config,
    model_type,
    tokenizer_type,
    cfg,
    adapter="lora",
    inference=False,
):
    # type: (str, str, str, str, AttrDefault, Optional[str], bool) -> Tuple[PreTrainedModel, PreTrainedTokenizer, Optional[PeftConfig]]

    # TODO refactor as a kwarg
    load_in_8bit = cfg.load_in_8bit
    tokenizer = None
    is_llama_derived_model = "llama" in base_model or (
        cfg.model_type and "llama" in cfg.model_type.lower()
    )

    if is_llama_derived_model and cfg.flash_attention:
        if cfg.device not in ["mps", "cpu"] and inference is False:
            from axolotl.flash_attn import replace_llama_attn_with_flash_attn

            logging.info("patching with flash attention")
            replace_llama_attn_with_flash_attn()
    elif is_llama_derived_model and cfg.xformers_attention:
        from alpaca_lora_4bit.monkeypatch.llama_attn_hijack_xformers import (
            hijack_llama_attention,
        )

        logging.info("patching with xformers attention")
        hijack_llama_attention()

    if cfg.bf16:
        torch_dtype = torch.bfloat16
    elif cfg.load_in_8bit or cfg.fp16:
        torch_dtype = torch.float16
    else:
        torch_dtype = torch.float32
    try:
        if cfg.load_4bit:
            from alpaca_lora_4bit.monkeypatch.peft_tuners_lora_monkey_patch import (
                replace_peft_model_with_int4_lora_model,
            )

            replace_peft_model_with_int4_lora_model()
        from peft import prepare_model_for_int8_training
    except Exception as e:
        logging.exception(e)
        raise e

    try:
        if cfg.load_4bit and is_llama_derived_model:
            from alpaca_lora_4bit.autograd_4bit import load_llama_model_4bit_low_ram
            from huggingface_hub import snapshot_download

            try:
                snapshot_download_kwargs = {}
                if cfg.base_model_ignore_patterns:
                    snapshot_download_kwargs[
                        "ignore_patterns"
                    ] = cfg.base_model_ignore_patterns
                cache_model_path = Path(
                    snapshot_download(base_model, **snapshot_download_kwargs)
                )
                files = (
                    list(cache_model_path.glob("*.pt"))
                    + list(cache_model_path.glob("*.safetensors"))
                    + list(cache_model_path.glob("*.bin"))
                )
                if len(files) > 0:
                    model_path = str(files[0])
                else:
                    logging.warning(
                        "unable to find a cached model file, this will likely fail..."
                    )
                    model_path = str(cache_model_path)
            except:
                model_path = cfg.base_model
            model, tokenizer = load_llama_model_4bit_low_ram(
                base_model_config if base_model_config else base_model,
                model_path,
                device_map=cfg.device_map,
                groupsize=cfg.gptq_groupsize if cfg.gptq_groupsize else -1,
                is_v1_model=cfg.gptq_model_v1
                if cfg.gptq_model_v1 is not None
                else True,
            )
            load_in_8bit = False
        elif is_llama_derived_model and "LlamaForCausalLM" in globals():
            model = LlamaForCausalLM.from_pretrained(
                base_model,
                load_in_8bit=cfg.load_in_8bit and cfg.adapter is not None,
                torch_dtype=torch_dtype,
                device_map=cfg.device_map,
            )
        elif model_type:
            model = getattr(transformers, model_type).from_pretrained(
                base_model,
                load_in_8bit=cfg.load_in_8bit and cfg.adapter is not None,
                torch_dtype=torch_dtype,
                device_map=cfg.device_map,
                trust_remote_code=True if cfg.trust_remote_code is True else False,
            )
        else:
            config = AutoConfig.from_pretrained(
                base_model,
                trust_remote_code=True if cfg.trust_remote_code is True else False,
            )
            model = AutoModelForCausalLM.from_pretrained(
                base_model,
                config=config,
                load_in_8bit=cfg.load_in_8bit and cfg.adapter is not None,
                torch_dtype=torch_dtype,
                device_map=cfg.device_map,
                trust_remote_code=True if cfg.trust_remote_code is True else False,
            )
    except Exception as e:
        logging.error(
            "Exception raised attempting to load model, retrying with AutoModelForCausalLM"
        )
        logging.exception(e)
        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            load_in_8bit=cfg.load_in_8bit and cfg.adapter is not None,
            torch_dtype=torch_dtype,
            device_map=cfg.device_map,
            trust_remote_code=True if cfg.trust_remote_code is True else False,
        )

    if not tokenizer:
        try:
            if is_llama_derived_model and "LlamaTokenizer" in globals():
                tokenizer = LlamaTokenizer.from_pretrained(model)
            else:
                tokenizer = getattr(transformers, tokenizer_type).from_pretrained(model)
        except:
            tokenizer = AutoTokenizer.from_pretrained(base_model_config)

    logging.debug(f"EOS: {tokenizer.eos_token_id} / {tokenizer.eos_token}")
    logging.debug(f"BOS: {tokenizer.bos_token_id} / {tokenizer.bos_token}")
    logging.debug(f"PAD: {tokenizer.pad_token_id} / {tokenizer.pad_token}")
    logging.debug(f"UNK: {tokenizer.unk_token_id} / {tokenizer.unk_token}")

    if tokenizer.__class__.__name__ in ["LlamaTokenizer", "LlamaTokenizerFast"]:
        tokenizer.pad_token = LLAMA_DEFAULT_PAD_TOKEN

    if tokenizer.__class__.__name__ == "GPTNeoXTokenizerFast":
        tokenizer.add_special_tokens({"pad_token": "[PAD]"})
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

    if cfg.tokens:
        for k, v in cfg.tokens.items():
            tokenizer.add_special_tokens({k: v})

    model.resize_token_embeddings(len(tokenizer))

    if cfg.adapter and load_in_8bit and not cfg.load_4bit:
        logging.info("converting PEFT model w/ prepare_model_for_int8_training")
        model = prepare_model_for_int8_training(model)

    model, lora_config = load_adapter(model, cfg, adapter)

    if cfg.ddp and not load_in_8bit:
        model.to(f"cuda:{cfg.local_rank}")

    if cfg.load_4bit:
        # Scales to half
        logging.info("Fitting 4bit scales and zeros to half")
        for n, m in model.named_modules():
            if "Autograd4bitQuantLinear" in str(type(m)) or "Linear4bitLt" in str(
                type(m)
            ):
                if hasattr(m, "is_v1_model") and m.is_v1_model:
                    m.zeros = m.zeros.half()
                m.scales = m.scales.half()
                m.bias = m.bias.half()

    if torch.cuda.device_count() > 1 and int(os.getenv("WORLD_SIZE", "1")) > 1:
        model.is_parallelizable = True
        model.model_parallel = True

    requires_grad = []
    for name, param in model.named_parameters(recurse=True):
        if param.requires_grad:
            requires_grad.append(f"{name}: {param.requires_grad}")
    if len(requires_grad) == 0:
        logging.warning("there are no parameters that require gradient updates")

    # TODO resume_from_checkpoint handling
    return model, tokenizer, lora_config


def load_adapter(model, cfg, adapter):
    # type: (PreTrainedModel, AttrDefault, Optional[str]) -> Tuple[PreTrainedModel, Optional[PeftConfig]]

    if adapter is None:
        return model, None
    if adapter == "lora":
        return load_lora(model, cfg)
    if adapter == "llama-adapter":
        return load_llama_adapter(model, cfg)

    raise NotImplementedError(f"{adapter} peft adapter not available")


def load_llama_adapter(model, cfg):
    # type: (PreTrainedModel, AttrDefault) -> Tuple[PreTrainedModel, Optional[PeftConfig]]
    from peft import (
        AdaptionPromptConfig,
        get_peft_model,
        PeftModel,
    )

    peft_config = AdaptionPromptConfig(
        adapter_layers=cfg.peft_adapter.layers,  # layers (L)
        adapter_len=cfg.peft_adapter.len,  # prompt length (K)
        task_type="CAUSAL_LM",
    )

    if cfg.peft_model_dir:
        model = PeftModel.from_pretrained(
            model,
            cfg.lora_model_dir,
            device_map=cfg.device_map,
            torch_dtype=torch.float16,
        )
    else:
        model = get_peft_model(model, peft_config)

    model.print_trainable_parameters()

    return model, peft_config


def load_lora(model, cfg):
    # type: (PreTrainedModel, AttrDefault) -> Tuple[PreTrainedModel, Optional[PeftConfig]]

    from peft import (
        LoraConfig,
        get_peft_model,
        PeftModel,
    )

    lora_config = None

    lora_config = LoraConfig(
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        target_modules=cfg.lora_target_modules,
        lora_dropout=cfg.lora_dropout,
        fan_in_fan_out=cfg.lora_fan_in_fan_out,
        bias="none",
        task_type="CAUSAL_LM",
    )

    if cfg.lora_model_dir:
        model = PeftModel.from_pretrained(
            model,
            cfg.lora_model_dir,
            device_map=cfg.device_map,
            torch_dtype=torch.float16,
        )
    else:
        model = get_peft_model(model, lora_config)

    model.print_trainable_parameters()

    return model, lora_config
