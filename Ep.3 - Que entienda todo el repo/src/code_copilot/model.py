"""Bedrock model factory.

Centralizes creation of the Amazon Bedrock model (Amazon Nova Pro) so the
model id, region, and generation parameters are configured in one place. The
cross-region US inference profile ``us.amazon.nova-pro-v1:0`` is used by
default, which is invocable from the project region (us-east-2).
"""

from __future__ import annotations

import os

# Default to the US cross-region inference profile for Nova Pro. Nova Pro is
# more reliable than Nova Lite for multi-step tool use (Nova Lite can emit
# malformed tool-use sequences on large arguments).
DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"
DEFAULT_REGION = "us-east-2"


def buildBedrockModel(temperature: float = 0.2, maxTokens: int = 2048):
    """Create a configured Strands ``BedrockModel`` for Amazon Nova Pro.

    Reads ``BEDROCK_MODEL_ID`` and ``AWS_REGION`` from the environment when set,
    falling back to Nova Pro in us-east-2. A low temperature is used by
    default because the agent's tasks (explaining and recommending) are factual.

    When ``AWS_PROFILE`` is set, an explicit boto3 session bound to that profile
    is passed to the model so credentials come from the named profile (for
    example ``my-Free-tier``) instead of the ``default`` profile.

    Args:
        temperature: Sampling temperature (lower is more deterministic).
        maxTokens: Maximum tokens to generate per response.

    Returns:
        A ``strands.models.BedrockModel`` instance.
    """
    from strands.models import BedrockModel

    modelId = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    regionName = os.environ.get("AWS_REGION", DEFAULT_REGION)
    profileName = os.environ.get("AWS_PROFILE")

    modelArguments: dict = {
        "model_id": modelId,
        "temperature": temperature,
        "max_tokens": maxTokens,
    }

    # Bind an explicit session to the named profile so the app authenticates
    # against the intended account (e.g. the free-tier project) rather than the
    # default profile. BedrockModel rejects passing both region_name and
    # boto_session, so the region is carried by the session in that case.
    if profileName:
        import boto3

        modelArguments["boto_session"] = boto3.Session(
            profile_name=profileName, region_name=regionName
        )
    else:
        modelArguments["region_name"] = regionName

    return BedrockModel(**modelArguments)


def runModelPrompt(promptText: str, *, temperature: float = 0.2, maxTokens: int = 2048) -> str:
    """Run a one-shot prompt against the Bedrock model and return the text.

    Used by the explain/recommend tools to synthesize natural language without
    spinning up a full sub-agent. Any failure (missing credentials, no model
    access, network error) is surfaced to the caller as an exception.
    """
    from strands import Agent

    synthesisAgent = Agent(
        model=buildBedrockModel(temperature=temperature, maxTokens=maxTokens),
        system_prompt=(
            "You are a precise senior software engineer. Answer using only the "
            "information provided. Do not invent details. Never reveal secret "
            "values; refer to them only by type and location."
        ),
    )
    result = synthesisAgent(promptText)
    return str(result)
