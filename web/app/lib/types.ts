// https://stackoverflow.com/questions/57683303/how-can-i-see-the-full-expanded-contract-of-a-typescript-type

// type Id<T> = T extends object ? {} & { [P in keyof T]: Id<T[P]> } : T

// expands object types one level deep
// eslint-disable-next-line @typescript-eslint/no-unused-vars
type Expand<T> = T extends infer O ? { [K in keyof O]: O[K] } : never
// expands object types recursively
// eslint-disable-next-line @typescript-eslint/no-unused-vars
type ExpandRecursively<T> = T extends object
  ? T extends infer O
    ? { [K in keyof O]: ExpandRecursively<O[K]> }
    : never
  : T

// usage: type ExpandedYourType = Expand<YourType>;
