// picker feature: composes its submodules and exposes one interface.
import { createList } from './picker/list.js';
import { createSheet } from './picker/sheet.js';

export function createPicker(ctx) {
  const list = createList({ ...ctx,
    sheet: { closePicker: (...args) => sheet.closePicker(...args), renderPickerButton: (...args) => sheet.renderPickerButton(...args) },
  });
  const sheet = createSheet({ ...ctx,
    list,
  });
  return { ...list, ...sheet };
}
