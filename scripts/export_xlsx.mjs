import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [source, target, previewDir] = process.argv.slice(2);
if (!source || !target) throw new Error('Expected input JSON and output XLSX paths');
const tables = JSON.parse(await fs.readFile(source, 'utf8'));
const workbook = Workbook.create();
const sheets = [];
function quantityNumberFormat(value) {
  // A positive requirement must never appear as zero because of formatting.
  if (typeof value === 'number' && value !== 0 && Math.abs(value) < 1e-6) {
    return '0.######E+00;[Red](0.######E+00);0';
  }
  return Number.isInteger(value) ? '#,##0;[Red](#,##0);0'
    : '#,##0.######;[Red](#,##0.######);0';
}
for (const [name, input] of Object.entries(tables)) {
  const sheet = workbook.worksheets.add(name);
  sheets.push(sheet);
  sheet.showGridLines = false;
  sheet.getRange('A2').values = [[name === 'План закупки' ? 'Проект закупки для проверки менеджером' : name]];
  sheet.getRange('A2').format.font = {bold:true,size:14,color:'#172B45'};
  sheet.getRange('A3').values = [['Снимок расчёта. Измените параметры в приложении и экспортируйте заново.']];
  const values = input.map(row => row.map(value => typeof value === 'string' && /^[=+@-]/.test(value.trimStart()) ? "'" + value : value));
  const range = sheet.getRangeByIndexes(4, 0, values.length, values[0].length);
  range.values = values;
  range.format.font = {name:'Arial',size:10,color:'#172B45'};
  range.format.columnWidth = 18;
  range.format.rowHeight = 30;
  range.format.verticalAlignment = 'center';
  const header = sheet.getRangeByIndexes(4, 0, 1, values[0].length);
  header.format.fill = '#172B45';
  header.format.font = {name:'Arial',size:10,color:'#FFFFFF',bold:true};
  header.format.wrapText = true;
  header.format.rowHeight = 38;
  sheet.freezePanes.freezeRows(5);
  if (name === 'План закупки') {
    sheet.getRange(`C5:C${values.length+4}`).format.columnWidth = 55;
    sheet.getRange(`C5:C${values.length+4}`).format.wrapText = true;
    sheet.getRange(`E5:E${values.length+4}`).format.columnWidth = 28;
    sheet.getRange(`O5:O${values.length+4}`).format.columnWidth = 95;
    sheet.getRange(`O5:O${values.length+4}`).format.wrapText = true;
    if(values.length>1) {
      sheet.getRange(`F6:L${values.length+4}`).format.numberFormat =
        values.slice(1).map(row => row.slice(5, 12).map(quantityNumberFormat));
      sheet.getRange(`M6:N${values.length+4}`).setNumberFormat('yyyy-mm-dd');
      for(let i=1;i<values.length;i++) {
        const lines=Math.max(Math.ceil(String(values[i][2]||'').length/48),Math.ceil(String(values[i][14]||'').length/86));
        sheet.getRangeByIndexes(i+4,0,1,15).format.rowHeight=Math.max(32,lines*13+12);
      }
    }
  } else {
    sheet.getRange(`A5:A${values.length+4}`).format.columnWidth = 46;
    sheet.getRange(`B5:B${values.length+4}`).format.columnWidth = 110;
    sheet.getRange(`A5:B${values.length+4}`).format.wrapText = true;
    sheet.getRange(`A6:B${values.length+4}`).format.rowHeight = 45;
    sheet.getRange('B6').setNumberFormat('yyyy-mm-dd');
    sheet.getRange('B7').setNumberFormat('yyyy-mm-dd hh:mm');
  }
}
workbook.recalculate();
if (previewDir) {
  await fs.mkdir(previewDir,{recursive:true});
  for (let i=0;i<sheets.length;i++) {
    const image=await workbook.render({sheetName:sheets[i].name,range:i===0?'A2:F8':'A2:B10',scale:1.4,format:'png'});
    await fs.writeFile(`${previewDir}/sheet-${i}.png`,new Uint8Array(await image.arrayBuffer()));
  }
  const quantities=await workbook.render({sheetName:sheets[0].name,range:'F5:L10',scale:1.4,format:'png'});
  await fs.writeFile(`${previewDir}/quantities.png`,new Uint8Array(await quantities.arrayBuffer()));
  const scan=await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#NULL!',options:{useRegex:true,maxResults:20}});
  console.log(scan.ndjson);
}
const output=await SpreadsheetFile.exportXlsx(workbook);
await output.save(target);
console.log('Exported '+target);
