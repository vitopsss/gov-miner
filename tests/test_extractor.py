import unittest

from gov_miner.extractor import extract_record
from gov_miner.problems import (
    build_problem_rankings,
    extract_problem_record,
    extract_problem_value,
    flatten_problem_records,
    select_manual_review_rows,
)


class ExtractorTest(unittest.TestCase):
    def test_extracts_core_tce_fine_record(self) -> None:
        text = """
        ACÓRDÃO Nº 2385/2025 - TCE - SEGUNDA CÂMARA
        Processo: Tomada de contas especial
        Órgão: Prefeitura Municipal de Manaus
        Responsável: JOÃO DA SILVA, Prefeito Municipal

        ACORDAM em julgar irregulares as contas, em razão da ausência
        de prestação de contas. Aplicar multa ao Sr. JOÃO DA SILVA,
        Prefeito Municipal, no valor de R$ 13.654,39.
        """

        record = extract_record(text).to_dict()

        self.assertEqual(
            record["numero_documento"],
            "ACÓRDÃO Nº 2385/2025 - TCE - SEGUNDA CÂMARA",
        )
        self.assertEqual(record["tipo_processo"], "Tomada de contas especial")
        self.assertEqual(record["orgao_auditado"], "Prefeitura Municipal de Manaus")
        self.assertEqual(record["veredito_contas"], "Irregular")
        self.assertEqual(record["motivo_sancao"], "Ausência de prestação de contas")
        self.assertEqual(record["valor_total_multas_aplicadas"], 13654.39)
        self.assertEqual(record["gestores_penalizados"][0]["nome"], "João da Silva")
        self.assertEqual(
            record["gestores_penalizados"][0]["cargo"],
            "Prefeito Municipal",
        )
        self.assertEqual(
            record["gestores_penalizados"][0]["valor_multa_individual"],
            13654.39,
        )

    def test_extracts_root_cause_problem_with_evidence_and_decimal_value(self) -> None:
        text = """
        ACÓRDÃO Nº 99/2025 - TCE - TRIBUNAL PLENO
        Processo: Prestação de contas
        Órgão: Prefeitura Municipal de Coari
        Responsável: MARIA DOS SANTOS, Secretária Municipal de Saúde

        Foram constatadas despesas sem comprovação suficiente, diante da ausência
        de notas fiscais e recibos referentes aos pagamentos realizados. Também
        houve glosa no valor de R$ 1.234,56 por dano ao erário.
        Aplicar multa à Sra. MARIA DOS SANTOS no valor de R$ 500,00.
        """

        record = extract_problem_record(text, source_file="amostra.pdf", chunk_index=1)

        self.assertEqual(record["municipio"], "Coari")
        self.assertEqual(record["secretaria"], "Saude")
        self.assertIsNone(record["classificacao_llm"])
        self.assertGreaterEqual(len(record["problemas_detectados"]), 1)
        self.assertTrue(
            all(problem["trechos_evidencia"] for problem in record["problemas_detectados"])
        )
        self.assertIn(
            "nota fiscal",
            {
                document
                for problem in record["problemas_detectados"]
                for document in problem["documentos_relacionados"]
            },
        )
        self.assertEqual(extract_problem_value(text), 1234.56)

    def test_problem_ranking_groups_by_document_and_category(self) -> None:
        record = extract_problem_record(
            """
            ACÓRDÃO Nº 100/2025 - TCE
            Processo: Tomada de contas especial
            Órgão: Prefeitura Municipal de Tefé
            Ausência de prestação de contas do convênio, sem extratos bancários.
            Dano ao erário no valor de R$ 2.000,00.
            """,
            source_file="tefe.pdf",
            chunk_index=1,
        )

        rows = flatten_problem_records([record])
        rankings = build_problem_rankings(rows)

        self.assertTrue(rows)
        self.assertTrue(rankings)
        self.assertGreaterEqual(rankings[0]["ocorrencias"], 1)
        self.assertIn("categoria", rankings[0])

    def test_selects_unique_rows_for_manual_review(self) -> None:
        rows = [
            {
                "_source_file": "a.pdf",
                "_chunk_index": 1,
                "numero_documento": "A",
                "categoria": "Dano ao erario e glosa",
                "valor_envolvido": 100.0,
                "valor_total_multas_aplicadas": 10.0,
                "trechos_evidencia": ["evidencia A"],
            },
            {
                "_source_file": "a.pdf",
                "_chunk_index": 2,
                "numero_documento": "A",
                "categoria": "Documentacao fiscal e comprovacao",
                "valor_envolvido": 50.0,
                "valor_total_multas_aplicadas": 10.0,
                "trechos_evidencia": ["evidencia A2"],
            },
            {
                "_source_file": "b.pdf",
                "_chunk_index": 1,
                "numero_documento": "B",
                "categoria": "Convenios e transferencias",
                "valor_envolvido": 80.0,
                "valor_total_multas_aplicadas": 0.0,
                "trechos_evidencia": ["evidencia B"],
            },
        ]

        review_rows = select_manual_review_rows(rows, limit=2)

        self.assertEqual(len(review_rows), 2)
        self.assertEqual(review_rows[0]["_source_file"], "a.pdf")
        self.assertEqual(review_rows[0]["trecho_evidencia"], "evidencia A")
        self.assertEqual(review_rows[1]["_source_file"], "b.pdf")


if __name__ == "__main__":
    unittest.main()
