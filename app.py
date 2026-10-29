#!/usr/bin/env python3
"""CDK app entrypoint for the Ambit codebook generator (HAE slice)."""
import os

import aws_cdk as cdk

from infra.cdk.ambit_stack import AmbitCodebookStack

app = cdk.App()

AmbitCodebookStack(
    app,
    "AmbitCodebook",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    description="AI-assisted medical codebook generator (HAE slice) - Ambit case study",
)

app.synth()
