from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SourcifyModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ChainInfo(SourcifyModel):
    name: str
    chain_id: int = Field(alias="chainId")
    supported: bool
    rpc: list[str] = Field(default_factory=list)


class SourceFile(SourcifyModel):
    content: str = ""


class CompilationInfo(SourcifyModel):
    language: str
    compiler: str | None = None
    compiler_version: str = Field(alias="compilerVersion")
    compiler_settings: dict[str, Any] = Field(default_factory=dict, alias="compilerSettings")
    name: str
    fully_qualified_name: str | None = Field(default=None, alias="fullyQualifiedName")


class BytecodeInfo(SourcifyModel):
    onchain_bytecode: str | None = Field(default=None, alias="onchainBytecode")
    recompiled_bytecode: str | None = Field(default=None, alias="recompiledBytecode")
    source_map: str | dict[str, Any] | None = Field(default=None, alias="sourceMap")


class DeploymentInfo(SourcifyModel):
    transaction_hash: str | None = Field(default=None, alias="transactionHash")
    block_number: str | None = Field(default=None, alias="blockNumber")
    transaction_index: str | None = Field(default=None, alias="transactionIndex")
    deployer: str | None = None


class ProxyImplementation(SourcifyModel):
    address: str
    name: str | None = None


class ProxyResolution(SourcifyModel):
    is_proxy: bool | None = Field(default=None, alias="isProxy")
    proxy_type: str | None = Field(default=None, alias="proxyType")
    implementations: list[ProxyImplementation] = Field(default_factory=list)


MatchStatus = Literal["match", "exact_match"]


class VerifiedContract(SourcifyModel):
    match: MatchStatus | None = None
    creation_match: MatchStatus | None = Field(default=None, alias="creationMatch")
    runtime_match: MatchStatus | None = Field(default=None, alias="runtimeMatch")
    chain_id: str = Field(alias="chainId")
    address: str
    verified_at: str | None = Field(default=None, alias="verifiedAt")
    match_id: str | None = Field(default=None, alias="matchId")
    abi: list[dict[str, Any]] = Field(default_factory=list)
    compilation: CompilationInfo | None = None
    sources: dict[str, SourceFile] = Field(default_factory=dict)
    creation_bytecode: BytecodeInfo | None = Field(default=None, alias="creationBytecode")
    runtime_bytecode: BytecodeInfo | None = Field(default=None, alias="runtimeBytecode")
    deployment: DeploymentInfo | None = None
    metadata: dict[str, Any] | None = None
    std_json_input: dict[str, Any] | None = Field(default=None, alias="stdJsonInput")
    userdoc: dict[str, Any] | None = None
    devdoc: dict[str, Any] | None = None
    proxy_resolution: ProxyResolution | None = Field(default=None, alias="proxyResolution")


class ErrorInfo(SourcifyModel):
    custom_code: str = Field(alias="customCode")
    message: str
    error_id: str | None = Field(default=None, alias="errorId")
    error_data: Any = Field(default=None, alias="errorData")


class VerificationSubmission(SourcifyModel):
    verification_id: str = Field(alias="verificationId")


class VerificationJob(SourcifyModel):
    is_job_completed: bool = Field(alias="isJobCompleted")
    verification_id: str = Field(alias="verificationId")
    contract: VerifiedContract | None = None
    error: ErrorInfo | None = None
    job_start_time: str | None = Field(default=None, alias="jobStartTime")
    job_finish_time: str | None = Field(default=None, alias="jobFinishTime")
    compilation_time: str | None = Field(default=None, alias="compilationTime")
