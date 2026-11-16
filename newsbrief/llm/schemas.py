from pydantic import BaseModel, ConfigDict, Field


class JobSummarySchema(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    cargo: str = Field(default="não informado", min_length=1)
    empresa: str = Field(default="não informado", min_length=1)
    local_modalidade: str = Field(default="não informado", min_length=1)
    faixa_salarial: str = Field(default="não informado", min_length=1)
    requisitos: list[str] = Field(default_factory=list)

    def format_text(self) -> str:
        reqs = ", ".join(self.requisitos[:4]) if self.requisitos else "não informado"
        return (
            f"{self.cargo} — {self.empresa}\n"
            f"- Local: {self.local_modalidade} | Salário: {self.faixa_salarial}\n"
            f"- Requisitos: {reqs}"
        )


class GrantSummarySchema(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    instituicao: str = Field(default="não informado", min_length=1)
    programa_nivel: str = Field(default="não informado", min_length=1)
    prazo_inscricao: str = Field(default="não informado", min_length=1)
    vagas_linhas: str = Field(default="não informado", min_length=1)

    def format_text(self) -> str:
        return (
            f"{self.programa_nivel} — {self.instituicao}\n"
            f"- Inscrições: {self.prazo_inscricao}\n"
            f"- Vagas/linhas: {self.vagas_linhas}"
        )
