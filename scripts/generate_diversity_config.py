# """
# For generating and processing
#     1> attribute values for Diversify X, and
#     2> named entity pools for Diversify Y
# """

# import argparse
# from scripts.utils import optimize_imports, add_argument, process_args, DATASET_NAME2TOPIC_DIM
# optimize_imports()

# from stefutil import get_logger, pl
# from src.generate.diversify import CategoryGenerator, EntityGenerator, Attribute2Categories, ENTITY_KEY_SEEDED


# logger = get_logger(__name__)


# def get_parser() -> argparse.ArgumentParser:
#     parser = argparse.ArgumentParser()
#     add_argument(parser, arg=[
#         'dataset_name', 'prompt_seed',
#         'chat_model_name', 'chat_max_tokens', 'chat_temperature', 'chat_seed', 'chat_logprobs', 'chat_timeout'
#     ])

#     # for sample diversity
#     parser.add_argument(
#         '--diversity_variant', required=True, type=str, choices=['diversify-x', 'diversify-y-vanilla', 'diversify-y-latent'],
#         help='The diversify variant to generate requirement configurations. One of [diversify-x, diversify-y-vanilla, diversify-y-latent].'
#     )
#     parser.add_argument(
#         '--n_call', required=True, type=int,
#         help='Number of OpenAI API calls to make for each group. '
#              'For Diversify X, each group is an attribute dimension; '
#              'For Diversify Y, each group is an (entity class) for the vanilla variant and '
#              'an (entity class, topic attribute value) tuple for the latent variant. '
#     )

#     # for the latent variant of Diversify Y
#     parser.add_argument(
#         '--diversify_y_latent_attribute', type=str,
#         help='The latent topic attribute values '
#              'accessed from the path of a Diversify X config file (see reproduce) '
#              'or a json string (topic attribute name => List of attribute values as `Dict[str, List[str]`)'
#              'for Diversify Y (latent). '
#              'If not given, defaults to the attribute values as reported in the paper.'
#     )
#     return parser


# if __name__ == '__main__':
#     def main():
#         parser = get_parser()
#         args = process_args(args=parser.parse_args())
#         logger.info(f'Running command w/ args {pl.i(vars(args), indent=1)}')

#         dnm, chat_args, variant, n_call = args.dataset_name, args.chat_args, args.diversity_variant, args.n_call

#         if variant == 'diversify-x':
#             gen = CategoryGenerator(dataset_name=dnm)
#         else:
#             assert 'diversify-y' in variant  # sanity check
#             seeded, a2c = variant == 'diversify-y-latent', None
#             if seeded and args.diversify_y_latent_attribute:
#                 lat_dim = DATASET_NAME2TOPIC_DIM[dnm]
#                 a2c = Attribute2Categories.from_json(
#                     dataset_name=dnm, diverse_context=False, diverse_entity='seeded',
#                     diverse_x_config=args.diversify_y_latent_attribute,
#                     **({ENTITY_KEY_SEEDED: dict(seed_category=lat_dim)})  # follow internal `Attribute2Categories` API
#                 )
#             gen = EntityGenerator(dataset_name=dnm, seeded=seeded, a2c=a2c)
#         out = gen.write_completions(n_prompt=n_call, **chat_args)
#         gen.process_completions(completions_dir_name=out.output_dir)
#     main()


"""
For generating and processing
    1> attribute values for Diversify X, and
    2> named entity pools for Diversify Y
"""

import argparse
import os
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

from scripts.utils import optimize_imports, add_argument, process_args, DATASET_NAME2TOPIC_DIM
optimize_imports()

from stefutil import get_logger, pl
from src.generate.diversify import CategoryGenerator, EntityGenerator, Attribute2Categories, ENTITY_KEY_SEEDED

logger = get_logger(__name__)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_argument(parser, arg=['dataset_name'])

    # Hugging Face model arguments
    parser.add_argument('--hf_model_name', required=True, type=str, help="Name of the Hugging Face model to use.")
    parser.add_argument('--max_tokens', type=int, default=100, help="Maximum number of tokens to generate.")
    parser.add_argument('--temperature', type=float, default=1.0, help="Sampling temperature for generation.")
    parser.add_argument('--top_p', type=float, default=0.95, help="Nucleus sampling cumulative probability.")
    parser.add_argument('--seed', type=int, default=42, help="Random seed for reproducibility.")

    # for sample diversity
    parser.add_argument(
        '--diversity_variant', required=True, type=str, choices=['diversify-x', 'diversify-y-vanilla', 'diversify-y-latent'],
        help='The diversify variant to generate requirement configurations. One of [diversify-x, diversify-y-vanilla, diversify-y-latent].'
    )
    parser.add_argument(
        '--n_call', required=True, type=int,
        help='Number of prompts to generate for each group. '
             'For Diversify X, each group is an attribute dimension; '
             'For Diversify Y, each group is an (entity class) for the vanilla variant and '
             'an (entity class, topic attribute value) tuple for the latent variant. '
    )

    # for the latent variant of Diversify Y
    parser.add_argument(
        '--diversify_y_latent_attribute', type=str,
        help='The latent topic attribute values '
             'accessed from the path of a Diversify X config file (see reproduce) '
             'or a json string (topic attribute name => List of attribute values as `Dict[str, List[str]`)'
             'for Diversify Y (latent). '
             'If not given, defaults to the attribute values as reported in the paper.'
    )
    return parser


class HuggingFaceGenerator:
    """
    Wrapper to generate text completions using a Hugging Face causal language model.
    """
    def __init__(self, model_name: str, max_tokens: int = 100, temperature: float = 1.0, top_p: float = 0.95, seed: int = 42):
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.seed = seed

        # Load tokenizer and model
        logger.info(f"Loading Hugging Face model: {model_name}")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(model_name).to(self.device)
        torch.manual_seed(self.seed)

    def generate(self, prompt: str, n_calls: int = 1) -> list:
        """
        Generate completions for a given prompt.
        :param prompt: The input prompt string.
        :param n_calls: Number of completions to generate.
        :return: A list of generated completions.
        """
        completions = []
        for _ in range(n_calls):
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
            output = self.model.generate(
                inputs.input_ids,
                max_length=inputs.input_ids.shape[1] + self.max_tokens,
                temperature=self.temperature,
                top_p=self.top_p,
                do_sample=True
            )
            decoded = self.tokenizer.decode(output[0], skip_special_tokens=True)
            completions.append(decoded)
        return completions


if __name__ == '__main__':
    def main():
        parser = get_parser()
        args = process_args(args=parser.parse_args())
        logger.info(f'Running command w/ args {pl.i(vars(args), indent=1)}')

        dnm, variant, n_call = args.dataset_name, args.diversity_variant, args.n_call

        # Initialize Hugging Face generator
        hf_generator = HuggingFaceGenerator(
            model_name=args.hf_model_name,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            seed=args.seed
        )

        if variant == 'diversify-x':
            gen = CategoryGenerator(dataset_name=dnm)
        else:
            assert 'diversify-y' in variant  # sanity check
            seeded, a2c = variant == 'diversify-y-latent', None
            if seeded and args.diversify_y_latent_attribute:
                lat_dim = DATASET_NAME2TOPIC_DIM[dnm]
                a2c = Attribute2Categories.from_json(
                    dataset_name=dnm, diverse_context=False, diverse_entity='seeded',
                    diverse_x_config=args.diversify_y_latent_attribute,
                    **({ENTITY_KEY_SEEDED: dict(seed_category=lat_dim)})  # follow internal `Attribute2Categories` API
                )
            gen = EntityGenerator(dataset_name=dnm, seeded=seeded, a2c=a2c)

        # Generate prompts and completions
        for setup in gen.iter_setups():
            prompt = gen.get_prompt(**setup.get_prompt_args)
            logger.info(f"Prompt:\n{prompt}")

            # Generate completions using Hugging Face model
            completions = hf_generator.generate(prompt=prompt, n_calls=n_call)

            # Save completions
            output_dir = setup.output_dir or "output"
            os.makedirs(output_dir, exist_ok=True)
            output_file = os.path.join(output_dir, f"{setup.key_name}_completions.txt")
            with open(output_file, "w") as f:
                for completion in completions:
                    f.write(completion + "\n")
            logger.info(f"Saved completions to {output_file}")

        # Process completions
        gen.process_completions(completions_dir_name=output_dir)

    main()
