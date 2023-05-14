from model import ExLlama, ExLlamaCache, ExLlamaConfig
from tokenizer import ExLlamaTokenizer
from generator import ExLlamaGenerator
import argparse
import torch
import sys
import os

# Simple interactive chatbot script

torch.set_grad_enabled(False)
torch.cuda._lazy_init()

# Parse arguments

parser = argparse.ArgumentParser(description = "Simple chatbot example for ExLlama")

parser.add_argument("-t", "--tokenizer", type = str, help = "Tokenizer model path", required = True)
parser.add_argument("-c", "--config", type = str, help = "Model config path (config.json)", required = True)
parser.add_argument("-m", "--model", type = str, help = "Model weights path (.pt or .safetensors file)", required = True)
parser.add_argument("-g", "--groupsize", type = int, help = "Groupsize for quantized weights", default = -1)

parser.add_argument("-a", "--attention", type = ExLlamaConfig.AttentionMethod.argparse, choices = list(ExLlamaConfig.AttentionMethod), help="Attention method", default = ExLlamaConfig.AttentionMethod.PYTORCH_SCALED_DP)
parser.add_argument("-mm", "--matmul", type = ExLlamaConfig.MatmulMethod.argparse, choices = list(ExLlamaConfig.MatmulMethod), help="Matmul method", default = ExLlamaConfig.MatmulMethod.SWITCHED)

parser.add_argument("-l", "--length", type = int, help = "Maximum sequence length", default = 2048)

parser.add_argument("-p", "--prompt", type = str, help = "Prompt file")
parser.add_argument("-un", "--username", type = str, help = "Display name of user", default = "User")
parser.add_argument("-bn", "--botname", type = str, help = "Display name of chatbot", default = "Chatbort")
parser.add_argument("-bf", "--botfirst", action = "store_true", help = "Start chat on bot's turn")

parser.add_argument("-nnl", "--no_newline", action = "store_true", help = "Do not break bot's response on newline (allow multi-paragraph responses)")
parser.add_argument("-temp", "--temperature", type = float, help = "Temperature", default = 0.95)
parser.add_argument("-topk", "--top_k", type = int, help = "Top-K", default = 20)
parser.add_argument("-topp", "--top_p", type = float, help = "Top-P", default = 0.65)
parser.add_argument("-minp", "--min_p", type = float, help = "Min-P", default = 0.06)
parser.add_argument("-repp",  "--repetition_penalty", type = float, help = "Repetition penalty", default = 1.15)
parser.add_argument("-repps", "--repetition_penalty_sustain", type = int, help = "Past length for repetition penalty", default = 256)

args = parser.parse_args()

# Some feedback

print(f" -- Loading model")
print(f" -- Tokenizer: {args.tokenizer}")
print(f" -- Model config: {args.config}")
print(f" -- Model: {args.model}")
print(f" -- Groupsize: {args.groupsize if args.groupsize != -1 else 'none'}")
print(f" -- Sequence length: {args.length}")
print(f" -- Temperature: {args.temperature:.2f}")
print(f" -- Top-K: {args.top_k}")
print(f" -- Top-P: {args.top_p:.2f}")
print(f" -- Min-P: {args.min_p:.2f}")
print(f" -- Repetition penalty: {args.repetition_penalty:.2f}")

print_opts = []
print_opts.append("attention: " + str(args.attention))
print_opts.append("matmul: " + str(args.matmul))
if args.no_newline: print_opts.append("no_newline")
if args.botfirst: print_opts.append("botfirst")

print(f" -- Options: {print_opts}")

username = args.username
bot_name = args.botname

if args.prompt is not None:
    with open(args.prompt, "r") as f:
        past = f.read()
        past = past.replace("{username}", username)
        past = past.replace("{bot_name}", bot_name)
        past = past.strip() + "\n"
else:
    past = f"{bot_name}: Hello, {username}\n"

# Instantiate model and generator

config = ExLlamaConfig(args.config)
config.model_path = args.model
config.groupsize = args.groupsize
config.attention_method = args.attention
config.matmul_method = args.matmul
if args.length is not None: config.max_seq_len = args.length

model = ExLlama(config)
cache = ExLlamaCache(model)

tokenizer = ExLlamaTokenizer(args.tokenizer)

generator = ExLlamaGenerator(model, tokenizer, cache)
generator.settings = ExLlamaGenerator.Settings()
generator.settings.temperature = args.temperature
generator.settings.top_k = args.top_k
generator.settings.top_p = args.top_p
generator.settings.min_p = args.min_p
generator.settings.token_repetition_penalty_max = args.repetition_penalty
generator.settings.token_repetition_penalty_sustain = args.repetition_penalty_sustain
generator.settings.token_repetition_penalty_decay = generator.settings.token_repetition_penalty_sustain // 2

break_on_newline = not args.no_newline

# Be nice to Chatbort

max_response_tokens = 256
extra_prune = 256

print(past, end = "")
ids = tokenizer.encode(past)
generator.gen_begin(ids)

next_userprompt = username + ": "

first_round = True

while True:

    res_line = bot_name + ":"
    res_tokens = tokenizer.encode(res_line)
    num_res_tokens = res_tokens.shape[-1]  # Decode from here

    if first_round and args.botfirst: in_tokens = res_tokens

    else:

        # Read and format input

        in_line = input(next_userprompt)
        in_line = username + ": " + in_line.strip() + "\n"

        next_userprompt = username + ": "

        # No need for this, really

        past += in_line

        # SentencePiece doesn't tokenize spaces separately so we can't know from individual tokens if they start a new word
        # or not. Instead, repeatedly decode the generated response as it's being built, starting from the last newline,
        # and print out the differences between consecutive decodings to stream out the response.

        in_tokens = tokenizer.encode(in_line)
        in_tokens = torch.cat((in_tokens, res_tokens), dim = 1)

    # If we're approaching the context limit, prune some whole lines from the start of the context. Also prune a
    # little extra so we don't end up rebuilding the cache on every line when up against the limit.

    expect_tokens = in_tokens.shape[-1] + max_response_tokens
    max_tokens = config.max_seq_len - expect_tokens
    if generator.gen_num_tokens() >= max_tokens:
        generator.gen_prune_to(config.max_seq_len - expect_tokens - extra_prune, tokenizer.newline_token_id)

    # Feed in the user input and "{bot_name}:", tokenized

    generator.gen_feed_tokens(in_tokens)

    # Generate with streaming

    print(res_line, end = "")
    sys.stdout.flush()

    for i in range(max_response_tokens):

        gen_token = generator.gen_single_token()
        token = gen_token
        if gen_token.item() == tokenizer.eos_token_id:
            token = torch.tensor([[tokenizer.newline_token_id]])

        generator.gen_accept_token(token)

        num_res_tokens += 1
        text = tokenizer.decode(generator.sequence[:, -num_res_tokens:][0])
        new_text = text[len(res_line):]
        res_line += new_text

        print(new_text, end="")
        sys.stdout.flush()

        if break_on_newline and gen_token.item() == tokenizer.newline_token_id: break
        if gen_token.item() == tokenizer.eos_token_id: break

        # GPT4All isn't always good at emitting an EOS token but will usually spit out the user prompt in any case,
        # so as a fallback for this and similarly trained models, catch that and roll back a few tokens.

        if res_line.endswith(f"{username}:"):
            plen = tokenizer.encode(f"{username}:").shape[-1]
            generator.gen_rewind(plen)
            next_userprompt = " "
            break

    past += res_line
    first_round = False