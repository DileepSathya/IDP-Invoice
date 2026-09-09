import { useEffect, useId, useRef, useState } from "react";
import { fetchMasterSuggestions, MasterKind, MasterSuggestion } from "../api";
import "./MasterNameInput.css";

type Props = {
  kind: MasterKind;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

/** Suggestions are optional: only typing or explicit selection changes the value. */
export function MasterNameInput({ kind, value, onChange, placeholder }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<MasterSuggestion[]>([]);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const options = useRef<Array<HTMLDivElement | null>>([]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setRows([]);
    setActive(-1);
    setError("");
    setLoading(true);
    const timer = window.setTimeout(() => {
      fetchMasterSuggestions(kind, value, controller.signal)
        .then((items) => { if (!controller.signal.aborted) setRows(items); })
        .catch(() => {
          if (!controller.signal.aborted) setError("Suggestions unavailable. You can still enter a name.");
        })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [kind, value, open]);

  useEffect(() => {
    if (active >= 0) options.current[active]?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const choose = (row: MasterSuggestion) => {
    onChange(row.name);
    setOpen(false);
    setActive(-1);
  };

  return (
    <span className="master-name-input">
      <input
        type="text"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-activedescendant={open && active >= 0 ? `${id}-${active}` : undefined}
        autoComplete="off"
        value={value}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(event) => {
          setRows([]);
          setActive(-1);
          setLoading(true);
          onChange(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            if (rows.length) setActive((index) => event.key === "ArrowDown"
              ? (index + 1) % rows.length : (index <= 0 ? rows.length - 1 : index - 1));
          } else if (event.key === "Enter" && open && active >= 0 && rows[active]) {
            event.preventDefault();
            choose(rows[active]);
          } else if (event.key === "Tab") setOpen(false);
        }}
      />
      {open && (
        <span className="master-name-panel">
          <span id={id} role="listbox" aria-label={kind === "items" ? "Tally stock items" : kind === "ledgers" ? "Tally expense ledgers" : "Tally vendors"}>
            {rows.map((row, index) => (
              <div
                key={`${row.id}-${index}`}
                id={`${id}-${index}`}
                ref={(node) => { options.current[index] = node; }}
                role="option"
                aria-selected={active === index}
                className="master-name-option"
                onMouseDown={(event) => event.preventDefault()}
                onClick={(event) => { event.preventDefault(); choose(row); }}
                onMouseEnter={() => setActive(index)}
              >
                <strong>{row.name}</strong>
                {row.detail && <small>{row.detail}</small>}
              </div>
            ))}
          </span>
          <span className="master-name-hint" role="status">
            {loading ? "Searching Tally masters…" : error || (rows.length
              ? "Select a name, or keep typing to narrow the list."
              : "No matching names. You can still enter a name.")}
          </span>
        </span>
      )}
    </span>
  );
}
