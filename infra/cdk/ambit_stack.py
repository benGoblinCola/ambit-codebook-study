"""Foundation stack for the Ambit codebook generator (HAE slice).

Scope of THIS module: the data + security foundation that everything else binds to.
  - An isolated VPC (no NAT). Aurora is reached through the RDS Data API over HTTPS,
    so Lambdas do NOT live in the VPC and we pay for no NAT gateway.
  - Aurora Serverless v2 PostgreSQL with the Data API enabled (reference tables +
    crosswalk joins = plain SQL; effective-date logic is trivial).
  - A versioned S3 bucket: one prefix per reference release (land + version).
  - Admin credentials in a generated Secrets Manager secret (no secrets in source).

Compute (per-component least-privilege Lambdas + Step Functions) is layered on in a
later module so this foundation can be deployed and validated on its own.
"""
from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_s3 as s3,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_secretsmanager as secrets,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


class AmbitCodebookStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Network -------------------------------------------------------
        # Isolated subnets only. The Data API is an AWS service endpoint, so nothing
        # needs a route to the internet to reach the database.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                )
            ],
        )

        # --- Reference-file store: versioned S3 ----------------------------
        ref_bucket = s3.Bucket(
            self,
            "RefData",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # --- Reference database: Aurora Serverless v2 + Data API -----------
        # s3_import_buckets associates a scoped read-only role so the cluster can
        # bulk-load the large FDA NDC files from S3 via the aws_s3 extension.
        db = rds.DatabaseCluster(
            self,
            "Db",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                # 16.8 is offered in us-east-1 and supports Serverless v2, the Data
                # API, and the aws_s3 import extension. .of(..., s3_import=True) tells
                # CDK the engine supports the S3-import role wired by s3_import_buckets.
                version=rds.AuroraPostgresEngineVersion.of("16.8", "16", s3_import=True),
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=2,
            serverless_v2_max_capacity=16,
            credentials=rds.Credentials.from_generated_secret("ambit_admin"),
            default_database_name="codebook",
            enable_data_api=True,
            s3_import_buckets=[ref_bucket],
            removal_policy=RemovalPolicy.DESTROY,  # throwaway demo; tear down cleanly
        )

        self.vpc = vpc
        self.db = db
        self.ref_bucket = ref_bucket

        # ===================================================================
        # Compute: per-component least-privilege Lambdas + Step Functions.
        # Each Lambda gets its OWN role with only the permissions it needs:
        #   - all use the codebook_app DB identity (rds-data on the cluster +
        #     read of ONLY the app secret), never the admin secret;
        #   - only `research` may call Bedrock;
        #   - only `publish` may write S3 (and only under codebooks/).
        # ===================================================================
        app_secret = secrets.Secret.from_secret_name_v2(self, "AppSecret", "ambit/codebook_app")
        code = lambda_.Code.from_asset("src")
        common_env = {
            "CLUSTER_ARN": db.cluster_arn,
            "APP_SECRET_ARN": app_secret.secret_arn,
            "DB_NAME": "codebook",
            "REF_BUCKET": ref_bucket.bucket_name,
            "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        }
        data_api = iam.PolicyStatement(
            actions=["rds-data:ExecuteStatement", "rds-data:BatchExecuteStatement",
                     "rds-data:BeginTransaction", "rds-data:CommitTransaction",
                     "rds-data:RollbackTransaction"],
            resources=[db.cluster_arn],
        )
        def mkfn(cid: str, handler: str, timeout: int = 120, memory: int = 512):
            fn = lambda_.Function(
                self, cid,
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=f"ambit_codebook.handlers.{handler}",
                code=code,
                timeout=Duration.seconds(timeout),
                memory_size=memory,
                environment=common_env,
                tracing=lambda_.Tracing.ACTIVE,
            )
            fn.add_to_role_policy(data_api)
            # grant_read matches the secret's full ...-?????? ARN (from_secret_name_v2
            # yields a suffix-less ARN that a hand-written policy would miss).
            app_secret.grant_read(fn)
            return fn

        research_fn = mkfn("ResearchFn", "research", timeout=180)
        research_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["arn:aws:bedrock:*::foundation-model/*",
                       f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*"],
        ))
        resolve_fn = mkfn("ResolveFn", "resolve_validate")
        build_fn = mkfn("BuildFn", "build_draft")
        eval_fn = mkfn("EvalFn", "eval_gate", timeout=300)
        publish_fn = mkfn("PublishFn", "publish", timeout=90)
        publish_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:PutObject"], resources=[ref_bucket.arn_for_objects("codebooks/*")]))

        def task(cid: str, fn):
            # Default payload = the whole state; payload_response_only => output is the
            # Lambda's return, so one state object threads through the machine.
            return tasks.LambdaInvoke(self, cid, lambda_function=fn, payload_response_only=True)

        t_research = task("ResearchCandidates", research_fn)
        t_resolve = task("ResolveAndValidate", resolve_fn)
        t_build = task("BuildDraft", build_fn)
        t_eval = task("EvalGate", eval_fn)
        t_publish = task("PublishVersion", publish_fn)

        draft_ready = sfn.Succeed(self, "DraftReady")
        eval_failed = sfn.Fail(self, "EvalFailed", error="EvalGateFailed",
                               cause="Eval suite did not pass; draft not published.")
        gate = (
            sfn.Choice(self, "EvalPassed?")
            .when(sfn.Condition.boolean_equals("$.eval.passed", False), eval_failed)
            .otherwise(t_publish.next(draft_ready))
        )
        definition = t_research.next(t_resolve).next(t_build).next(t_eval).next(gate)
        state_machine = sfn.StateMachine(
            self, "CodebookPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(15),
            tracing_enabled=True,
        )
        self.state_machine = state_machine

        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "ClusterArn", value=db.cluster_arn)
        CfnOutput(self, "SecretArn", value=db.secret.secret_arn)
        CfnOutput(self, "BucketName", value=ref_bucket.bucket_name)
        CfnOutput(self, "DbName", value="codebook")
        CfnOutput(self, "RegionOut", value=self.region)
