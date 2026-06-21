"""PDF 报告生成服务（设计 §12，落实 Task 25）。

Jinja2 渲染九区块 HTML 报告，weasyprint 转 PDF（A4 + CJK 字体经系统字体渲染）。
前端 ECharts 图表以 base64 内联图传入；缺图时退化为纯数据报告（仍可导出）。

脱敏：报告数据源自三层结果模型，落库时已剥离 Key/敏感头，本层不再触敏感信息。
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_TEMPLATE_NAME = "report.html"


class PdfService:
    """报告 HTML 渲染与 PDF 生成。"""

    def __init__(self, template_dir: Path | None = None) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir or _TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render_html(self, report: dict, charts: dict[str, str] | None = None) -> str:
        """渲染报告 HTML；charts 为 {名称: data-uri} 的内联图字典（可空）。"""
        template = self._env.get_template(_TEMPLATE_NAME)
        return template.render(
            task=report.get("task", {}),
            model=report.get("model"),
            summary=report.get("summary"),
            strategies=report.get("strategies", []),
            charts=charts or {},
        )

    def generate_pdf(self, report: dict, charts: dict[str, str] | None = None) -> bytes:
        """渲染并转为 PDF 字节流。"""
        # 延迟导入：weasyprint 依赖原生库，仅在真正导出时加载，避免影响无 PDF 场景启动
        from weasyprint import HTML

        html = self.render_html(report, charts)
        return HTML(string=html).write_pdf()
