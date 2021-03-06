import argparse
import functools
import html
import json
import os
import random
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import npyscreen
import requests
from bs4 import BeautifulSoup
from fdfgen import forge_fdf


def main(*args):
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input-pdf', action='store', default='fahrgastrechte.pdf',
        help="Filename of the input pdf. (default: fahrgastrechte.pdf)")
    parser.add_argument('--pdftk', action='store', default='pdftk',
        help="Name/Path of the pdftk binary (default: pdftk)")
    parser.add_argument('--output-fdf', action='store', default='data.fdf',
        help="Filename of the fdf file generated to fill the pdf-form (default: data.fdf)")
    parser.add_argument('-o', '--output-pdf', action='store', default=None,
        help="Filename to store the filled pdf (default: fahrgastrechte_{CURRENT UNIT TIMESTAMP}.pdf)")
    parser.add_argument('--output-json', action='store', default='field.json',
        help="Filename to store the filled fields as a json file (default: fields.json)")
    parser.add_argument('-d', '--field-defaults', action='store', default='defaults.json',
        help="Filename to load the default value from (files from --output-json can be used) (default: default.json)")
    parser.add_argument('-a', '--auftragsnummer', action='store', default=None,
        help="Six character booking number")
    parser.add_argument('-n', '--nachname', action='store', default=None,
        help="Surname for the Booking")

    args = parser.parse_args()

    if os.path.isfile(args.field_defaults):
        with open(args.field_defaults, "r") as f:
            defaults = json.load(f)
    else:
        defaults = {}

    if args.auftragsnummer and args.nachname:
        defaults.update(download_buchung(**vars(args)))

    return npyscreen.wrapper_basic(functools.partial(run_menu, defaults, args))

def run_menu(defaults, args, *arg, **kwargs):
    field_fields = []
    fields, fieldnames = get_form_fields(**vars(args))
    F = npyscreen.FormMultiPage()
    for n in fieldnames:
        title = ""
        if 'FieldNameAlt' in fields[n]:
            title = fields[n]['FieldNameAlt']

        if n in defaults:
            value = defaults[n]
        else:
            value = None

        if 'FieldStateOption' in fields[n]:
            if value is not None:
                i = fields[n]['FieldStateOption'].index(value)
            else:
                i = fields[n]['FieldStateOption'].index('Off')
            if title:
                field_fields.append((n, F.add_widget_intelligent(
                    npyscreen.TitleMultiLine, values=fields[n]['FieldStateOption'], name=title, value=i)))
            else:
                field_fields.append((n, F.add_widget_intelligent(
                    npyscreen.MultiLine, values=fields[n]['FieldStateOption'], value=i)))
        else:
            field_fields.append((n, F.add_widget_intelligent(
                npyscreen.TitleText, name=title, value=value)))

    F.add_widget_intelligent(npyscreen.ButtonPress, name="Generate Form",
                             when_pressed_function=functools.partial(setattr, F, 'editing', False))
    F.switch_page(0)
    F.edit()

    fields = [(n, get_value(x)) for n, x in field_fields]

    return generate_form(fields, **vars(args))

def get_form_fields(pdftk, input_pdf, *args, **kwrags):
    fields = {}
    fieldnames = []
    fields_ = subprocess.check_output([pdftk, input_pdf, "dump_data_fields"], stderr=open("/dev/null", "w")).decode().split("---\n")
    for raw_field in fields_:
        f = {}
        for line in raw_field.splitlines():
            r = line.split(": ")
            r[1] = html.unescape(r[1])
            if r[0] in f:
                if not isinstance(f[r[0]], list):
                    f[r[0]] = [f[r[0]]]
                f[r[0]].append(r[1])
            else:
                f[r[0]] = r[1]
        if 'FieldName' in f:
            fields[f['FieldName']] = f
            fieldnames.append(f['FieldName'])

    return fields, fieldnames


def generate_form(fields, input_pdf, output_pdf, output_fdf, output_json, pdftk, *args, **kwargs):

    with open("fields.json", "w") as f:
        json.dump({x: y for x, y in fields}, f)

    fdf = forge_fdf("", fields, [], [], [])
    fdf_file = open(output_fdf, "wb")
    fdf_file.write(fdf)
    fdf_file.close()

    output_file = output_pdf if output_pdf else "fahrgastrechte_{}.pdf".format(int(time.time()))
    subprocess.run([pdftk, input_pdf, "fill_form", output_fdf, "output", output_file], stderr=open("/dev/null", "w"))

    return output_file

def request_xml(request_type, xml):
    url = 'https://fahrkarten.bahn.de/mobile/dbc/xs.go'
    tnr = random.getrandbits(64)
    print(tnr)
    ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    print(ts)
    request_body = '<{0} version="2.0"><rqheader tnr="{1}" app="NAVIGATOR" ts="{2}" l="en" v="17080000" os="Android REL 25" d="OnePlus A0001"/>{3}</{0}>'.format(request_type, tnr, ts, xml)
    print(request_body)
    return requests.post(url, data=request_body)

def parse_time_location(root, elem_id):
    arrival = root.iter(elem_id).__next__()
    bhf = arrival.find('ebhf_name').text
    date = datetime.strptime(arrival.get('dt'), '%Y-%m-%dT%H:%M:%S').date()
    time = datetime.strptime(arrival.get('t'), '%H:%M:%S').time()
    return bhf, date, time

def download_buchung(auftragsnummer, nachname, *args, **kwargs):
    request_body = '<rqorder on="{}"/><authname tln="{}"/>'.format(auftragsnummer, nachname)
    req = request_xml("rqorderdetails", request_body)
    root = ET.fromstring(req.text)

    arrival = parse_time_location(root, "arr")
    departure = parse_time_location(root, "dep")

    return {
        "S1F1": str(departure[1].day).zfill(2),
        "S1F2": str(departure[1].month).zfill(2),
        "S1F3": str(departure[1].year)[2:].zfill(2),
        "S1F4": str(departure[0]),
        "S1F5": str(departure[2].hour).zfill(2),
        "S1F6": str(departure[2].minute).zfill(2),
        "S1F7": str(arrival[0]),
        "S1F8": str(arrival[2].hour).zfill(2),
        "S1F9": str(arrival[2].minute).zfill(2),
        "S1F10": str(arrival[1].day).zfill(2),
        "S1F11": str(arrival[1].month).zfill(2),
        "S1F12": str(arrival[1].year)[2:].zfill(2),
    }

def get_value(w):
    if isinstance(w, npyscreen.MultiLine) or isinstance(w, npyscreen.TitleMultiLine):
        if w.value == None:
            return "Off"
        else:
            return w.values[w.value]
    else:
        return w.value

if __name__ == '__main__':
    print(main())
