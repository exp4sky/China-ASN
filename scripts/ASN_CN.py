#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
from html.parser import HTMLParser
import re
import urllib.request
import operator
from functools import reduce
import argparse
import logging
import time
from typing import Set, List

# ---- 配置项 ----
_version = '1.1'
# 数据源配置
DATAS_SOURCE = {
    'ipip': 'https://whois.ipip.net/countries/CN',
    'apnic': 'https://ftp.apnic.net/stats/apnic/delegated-apnic-latest',
    'he': 'https://bgp.he.net/country/CN'
}
# 请求头
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.81 Safari/537.36 Edg/104.0.1293.47'
}
# 网络请求配置
REQUEST_TIMEOUT = 30  # 超时时间（秒）
REQUEST_RETRY = 3     # 重试次数
# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
# ---- 配置结束 ----


def main(args):
    cn_asn: Set[int] = set()
    total_sources = len(args.source)
    logger.info(f"开始抓取中国ASN列表，数据源：{args.source}（共{total_sources}个）")

    # 遍历指定的数据源
    for source in args.source:
        try:
            if source == 'he':
                logger.info(f"正在抓取 HE 数据源：{DATAS_SOURCE['he']}")
                table = get_table_from_url(DATAS_SOURCE['he'])
                table = reduce(operator.add, table) if table else []
                asn_count = parse_asn_from_table(table, source)
                cn_asn.update(asn_count)
                logger.info(f"HE 数据源抓取完成，新增 ASN 数量：{len(asn_count)}")

            elif source == 'ipip':
                logger.info(f"正在抓取 IPIP 数据源：{DATAS_SOURCE['ipip']}")
                table = get_table_from_url(DATAS_SOURCE['ipip'])
                table = reduce(operator.add, table) if table else []
                asn_count = parse_asn_from_table(table, source)
                cn_asn.update(asn_count)
                logger.info(f"IPIP 数据源抓取完成，新增 ASN 数量：{len(asn_count)}")

            elif source == 'apnic':
                logger.info(f"正在抓取 APNIC 数据源：{DATAS_SOURCE['apnic']}")
                asn_count = parse_asn_from_apnic(DATAS_SOURCE['apnic'])
                cn_asn.update(asn_count)
                logger.info(f"APNIC 数据源抓取完成，新增 ASN 数量：{len(asn_count)}")

        except Exception as e:
            logger.error(f"数据源 {source} 抓取失败：{str(e)}", exc_info=False)
            # 非关键数据源失败不终止脚本
            continue

    # 生成配置文件
    if not cn_asn:
        logger.error("所有数据源抓取失败，未获取到任何ASN！")
        raise RuntimeError("No ASN data found from any source")

    cn_asn_sorted = sorted(cn_asn)
    logger.info(f"所有数据源处理完成，去重后总 ASN 数量：{len(cn_asn_sorted)}")
    generate_asn_config(cn_asn_sorted, args.output)
    logger.info(f"配置文件已生成：{args.output}")


def do_request(url: str) -> urllib.request.addinfourl:
    """带重试和超时的网络请求"""
    retry = 0
    while retry < REQUEST_RETRY:
        try:
            req = urllib.request.Request(url=url, headers=HEADERS)
            response = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
            logger.debug(f"请求 {url} 成功（重试次数：{retry}）")
            return response
        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as e:
            retry += 1
            logger.warning(f"请求 {url} 失败（重试 {retry}/{REQUEST_RETRY}）：{str(e)}")
            time.sleep(2 ** retry)  # 指数退避重试
    raise Exception(f"请求 {url} 超时，已重试 {REQUEST_RETRY} 次")


def get_table_from_url(url: str, index: int = 0) -> List[List[str]]:
    """解析URL中的HTML表格数据（修复原脚本硬编码bug）"""
    try:
        with do_request(url) as response:  # 自动释放资源
            html = response.read().decode('utf-8', errors='ignore')  # 容错编码错误

        parser = HTMLTableParser()
        parser.feed(html)
        if len(parser.tables) <= index:
            logger.warning(f"URL {url} 中未找到索引为 {index} 的表格")
            return []
        return parser.tables[index]
    except Exception as e:
        logger.error(f"解析 {url} 表格失败：{str(e)}", exc_info=False)
        return []


def parse_asn_from_table(table: List[str], source: str) -> Set[int]:
    """从表格数据中提取ASN号"""
    asn_set = set()
    for item in table:
        match = re.match(r'AS(\d+)', str(item))
        if match:
            asn = int(match.group(1))
            asn_set.add(asn)
    return asn_set


def parse_asn_from_apnic(url: str) -> Set[int]:
    """从APNIC数据源提取ASN号"""
    with do_request(url) as response:
        # 忽略编码错误，兼容非UTF-8数据
        content = response.read().decode('utf-8', errors='ignore')
    # 匹配 apnic|CN|asn|数字|... 格式
    results = re.findall(r'apnic\|CN\|asn\|(\d+?)\|', content, re.S)
    return {int(item) for item in results if item.strip().isdigit()}


def generate_asn_config(asn_list: List[int], output_file: str):
    """生成BIRD配置文件"""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('define china_asn = [\n')
        for idx, asn in enumerate(asn_list):
            # 最后一行不加逗号
            line = f'    {asn}{"" if idx == len(asn_list)-1 else ","}\n'
            f.write(line)
        f.write('];\n')


class HTMLTableParser(HTMLParser):
    """HTML表格解析器（原逻辑保留，增加注释）"""
    def __init__(self, decode_html_entities: bool = False, data_separator: str = ' '):
        super().__init__(convert_charrefs=decode_html_entities)
        self._data_separator = data_separator
        self._in_td = False
        self._in_th = False
        self._current_table = []
        self._current_row = []
        self._current_cell = []
        self.tables = []
        self.named_tables = {}
        self.name = ""

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.name = next((a[1] for a in attrs if a[0] == "id"), "")
        if tag == 'td':
            self._in_td = True
        if tag == 'th':
            self._in_th = True

    def handle_data(self, data):
        if self._in_td or self._in_th:
            self._current_cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'td':
            self._in_td = False
        elif tag == 'th':
            self._in_th = False

        if tag in ['td', 'th']:
            final_cell = self._data_separator.join(self._current_cell).strip()
            self._current_row.append(final_cell)
            self._current_cell = []
        elif tag == 'tr':
            self._current_table.append(self._current_row)
            self._current_row = []
        elif tag == 'table':
            self.tables.append(self._current_table)
            if self.name:
                self.named_tables[self.name] = self._current_table
            self._current_table = []
            self.name = ""


class CustomHelpFormatter(argparse.HelpFormatter):
    """自定义参数帮助格式"""
    def _format_action_invocation(self, action):
        if not action.option_strings or action.nargs == 0:
            return super()._format_action_invocation(action)
        default = self._get_default_metavar_for_optional(action)
        args_string = self._format_args(action, default)
        return ', '.join(action.option_strings) + ' ' + args_string


if __name__ == "__main__":
    def fmt(prog): return CustomHelpFormatter(prog)
    parser = argparse.ArgumentParser(
        formatter_class=fmt,
        description='Generate China ASN list for BIRD (优化版，支持重试和日志)'
    )
    parser.add_argument(
        '-o', '--output',
        metavar="<file>",
        default='asn_cn.conf',
        help='write to file (default: asn_cn.conf)'
    )
    parser.add_argument(
        '-s', '--source',
        choices=['apnic', 'he', 'ipip'],
        default=['apnic', 'he', 'ipip'],
        nargs='*',
        help='multiple sources can be used at the same time (default: apnic he ipip)'
    )
    parser.add_argument(
        '-v', '--version',
        action='version',
        version=f'%(prog)s {_version}'
    )
    args = parser.parse_args()
    main(args)
